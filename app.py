# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, jsonify, abort, g, Response
from sqlalchemy import text
from datetime import datetime, date, timedelta
from functools import wraps
import base64
import json
import logging
import math
import os
import pytz
import requests as http_requests

from src.database_setup import get_engine

app = Flask(__name__)
logger = logging.getLogger(__name__)
engine = get_engine()

GEOCODIO_API_KEY = os.getenv("GEOCODIO_API_KEY")
ORS_API_KEY      = os.getenv("ORS_API_KEY")

# SharePoint / Graph API
SP_TENANT_ID     = os.getenv("SHAREPOINT_TENANT_ID")
SP_CLIENT_ID     = os.getenv("SHAREPOINT_CLIENT_ID")
SP_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")
SP_SITE_URL      = os.getenv("SHAREPOINT_SITE_URL")   # e.g. https://anitascorp.sharepoint.com/sites/catering
SP_DRIVE_ID      = os.getenv("SHAREPOINT_DRIVE_ID")   # document library drive ID
SP_MAX_BYTES     = 25 * 1024 * 1024                    # 25 MB upload cap


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.before_request
def load_user():
    """Decode EasyAuth principal once per request and store on g.user."""
    user_id = request.headers.get("X-Ms-Client-Principal-Id")

    if not user_id:
        # Local dev — no EasyAuth headers present
        g.user = {"user_id": "dev", "username": "dev@local", "roles": ["admin"], "is_admin": True}
        return

    roles = []
    principal_encoded = request.headers.get("X-Ms-Client-Principal")
    if principal_encoded:
        try:
            principal = json.loads(base64.b64decode(principal_encoded))
            roles = [
                c["val"] for c in principal.get("claims", [])
                if c.get("typ") == "roles"
            ]
        except Exception as e:
            logger.warning(f"Failed to decode X-Ms-Client-Principal: {e}")

    g.user = {
        "user_id":  user_id,
        "username": request.headers.get("X-Ms-Client-Principal-Name", ""),
        "roles":    roles,
        "is_admin": "admin" in roles,
    }


@app.context_processor
def inject_user():
    """Make g.user available as 'user' in every template automatically."""
    return {"user": g.user}


def role_required(*roles):
    """
    Decorator that checks g.user has at least one of the required roles.
    Usage: @role_required("admin")  or  @role_required("admin", "gm")
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not any(role in g.user["roles"] for role in roles):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(value):
    """Format a numeric value as a currency string with commas."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _haversine_miles(lat1, lon1, lat2, lon2):
    """Straight-line distance in miles between two coordinate pairs."""
    R = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _estimate_travel_minutes(lat1, lon1, lat2, lon2):
    """
    Estimate driving time in minutes using Haversine + road factor.
    Road factor 1.25, average speed 28 mph — tuned for Northern Virginia.
    Returns minutes rounded to nearest 5.
    """
    straight = _haversine_miles(lat1, lon1, lat2, lon2)
    road_dist = straight * 1.25
    minutes   = (road_dist / 28) * 60
    return int(round(minutes / 5) * 5)


def _geocode_address(address):
    """
    Geocode a street address using Geocodio.
    Returns (lat, lon) or (None, None) on failure.
    """
    if not GEOCODIO_API_KEY:
        logger.warning("[GEOCODE] GEOCODIO_API_KEY not set — cannot geocode")
        return None, None
    try:
        logger.info(f"[GEOCODE] Calling Geocodio for: '{address}'")
        resp = http_requests.get(
            "https://api.geocod.io/v1.7/geocode",
            params={"q": address, "api_key": GEOCODIO_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            loc = results[0]["location"]
            lat, lon = float(loc["lat"]), float(loc["lng"])
            logger.info(f"[GEOCODE] Success: {lat}, {lon}")
            return lat, lon
        else:
            logger.warning(f"[GEOCODE] No results returned for: '{address}'")
    except Exception as e:
        logger.error(f"[GEOCODE] Error for '{address}': {e}")
    return None, None


def _get_ors_route(store_lat, store_lon, delivery_lat, delivery_lon):
    """
    Fetch actual driving route from OpenRouteService.
    Returns (route_geojson, distance_miles, duration_minutes) or (None, None, None) on failure.
    """
    if not ORS_API_KEY:
        logger.warning("[ORS] ORS_API_KEY not set — cannot route")
        print("[ORS] ORS_API_KEY not set — cannot route")
        return None, None, None
    try:
        url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
        headers = {
            "Authorization": ORS_API_KEY,
            "Content-Type": "application/json",
        }
        body = {
            "coordinates": [
                [store_lon, store_lat],
                [delivery_lon, delivery_lat],
            ],
        }
        logger.info(f"[ORS] Requesting route: {store_lat},{store_lon} -> {delivery_lat},{delivery_lon}")
        print(f"[ORS] Requesting route: {store_lat},{store_lon} -> {delivery_lat},{delivery_lon}")

        resp = http_requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"[ORS] Raw response keys: {list(data.keys())}")

        features = data.get("features", [])
        if features:
            feature          = features[0]
            props            = feature["properties"]["summary"]
            geojson          = feature["geometry"]
            distance_miles   = round(props["distance"] / 1609.34, 1)
            duration_minutes = int(round(props["duration"] / 60 / 5) * 5)
            logger.info(f"[ORS] Success: {distance_miles} mi, {duration_minutes} min")
            print(f"[ORS] Success: {distance_miles} mi, {duration_minutes} min")
            return geojson, distance_miles, duration_minutes
        else:
            logger.warning("[ORS] No features in response")
            print("[ORS] No features in response")
    except Exception as e:
        logger.warning(f"[ORS] Failed — falling back to Haversine: {e}")
        print(f"[ORS] Failed — falling back to Haversine: {e}")
    return None, None, None


def _cache_coordinates(order_guid, lat, lon):
    """Store geocoded coordinates back into order_delivery_info."""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE order_delivery_info
                SET latitude = :lat, longitude = :lon
                WHERE order_guid = :guid
            """), {"lat": lat, "lon": lon, "guid": order_guid})
        logger.info(f"[GEOCODE] Cached coordinates for order {order_guid}")
    except Exception as e:
        logger.error(f"[GEOCODE] Failed to cache coordinates for {order_guid}: {e}")

def _get_dining_options():
    """Load all dining options for the filter dropdown."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT guid::text, name
            FROM dining_options
            WHERE name LIKE '%Catering%'
            ORDER BY name
        """)).mappings().all()
    return [dict(r) for r in rows]

def _get_locations():
    """Load all locations for the store filter dropdown."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT store_guid::text AS guid, location_name AS name
            FROM locations
            ORDER BY location_name
        """)).mappings().all()
    return [dict(r) for r in rows]


def _get_store_locations_for_user(user):
    """
    Returns a filtered list of locations the user is allowed to see,
    or all locations for admin/catering/gm.

    Supports two patterns:
    1. store role + email: vienna@anitascorp.com matched against locations.contact_email
    2. store_ role (future): store_vienna matched against location_name
    """
    roles = user["roles"]

    # Full access roles
    if any(r in roles for r in ["admin", "catering", "gm"]):
        return _get_locations()

    allowed_guids = set()

    # Pattern 1 — store role, match by email
    if "store" in roles:
        email = user.get("username", "").lower().strip()
        if email:
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT store_guid::text AS guid, location_name AS name
                    FROM locations
                    WHERE LOWER(contact_email) = :email
                    ORDER BY location_name
                """), {"email": email}).mappings().all()
            for r in rows:
                allowed_guids.add(r["guid"])

    # Pattern 2 — store_ roles (future proofing)
    store_names = [r.replace("store_", "").lower() for r in roles if r.startswith("store_")]
    if store_names:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT store_guid::text AS guid, location_name AS name
                FROM locations
                WHERE LOWER(location_name) = ANY(:store_names)
                ORDER BY location_name
            """), {"store_names": store_names}).mappings().all()
        for r in rows:
            allowed_guids.add(r["guid"])

    # Return matched locations in name order
    if allowed_guids:
        all_locs = _get_locations()
        return [l for l in all_locs if l["guid"] in allowed_guids]

    return []

def _get_client_types():
    """Load all active catering client types for the dropdown."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT name
            FROM catering_client_types
            WHERE active = TRUE
            ORDER BY sort_order, name
        """)).mappings().all()
    return [r["name"] for r in rows]


def to_date_str(val):
    """Convert a date/datetime/string to YYYY-MM-DD string or None."""
    if val is None:
        return None
    if hasattr(val, 'isoformat'):
        return val.isoformat()[:10]
    s = str(val).strip()
    if len(s) >= 10 and s[4] == '-':
        return s[:10]
    return None


def _get_drivers_by_location():
    """
    Returns a dict of {location_id (str): [driver display names]}
    for all active drivers with store assignments.
    Uses nickname if set, otherwise full_name.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                cdl.store_guid::text AS location_id,
                COALESCE(NULLIF(cd.nickname, ''), cd.full_name) AS display_name
            FROM catering_driver_locations cdl
            JOIN catering_drivers cd ON cd.id = cdl.driver_id
            WHERE cd.active = TRUE
            ORDER BY cdl.store_guid, display_name
        """)).mappings().all()

    result = {}
    for row in rows:
        loc = row["location_id"]
        if loc not in result:
            result[loc] = []
        result[loc].append(row["display_name"])
    return result

def _get_store_orders(start_date, end_date, location_guids=None, dining_option_guids=None):
    """
    Same as _get_orders but filtered by location(s) and without route grouping.
    Returns { location_name: {"orders": [...], "location_total": "$x,xxx.xx"} }
    """
    location_filter = ""
    dining_filter   = ""
    params = {"start_date": start_date, "end_date": end_date}

    if location_guids:
        location_filter = "AND oh.location_id::text = ANY(:location_guids)"
        params["location_guids"] = location_guids
    if dining_option_guids:
        dining_filter = "AND oh.dining_option_guid::text = ANY(:dining_option_guids)"
        params["dining_option_guids"] = dining_option_guids

    sql = text(f"""
        SELECT
            oh.order_guid,
            oh.estimated_fulfillment_date,
            oh.location_id::text AS location_id,
            l.location_name,
            l.route,

            UPPER(oc.customer_first) AS customer_first,
            UPPER(oc.customer_last)  AS customer_last,
            oc.total_amount,

            COALESCE(
                NULLIF(cd.service_type, ''),
                CASE
                    WHEN do_.name ILIKE '%delivery%' THEN 'DEL'
                    WHEN do_.name ILIKE '%pickup%'   THEN 'PICK UP'
                    ELSE NULL
                END
            ) AS service_type,
            cd.travel_time,
            cd.departure_time,
            cd.arrival_time,
            cd.duration,
            cd.return_time,
            cd.num_employees,
            cd.driver_assigned,
            cd.event_company,
            cd.notes

        FROM orders_head oh
        JOIN locations l
            ON oh.location_id::text = l.store_guid::text
        LEFT JOIN LATERAL (
            SELECT customer_first, customer_last, total_amount
            FROM order_checks
            WHERE order_guid = oh.order_guid
            ORDER BY opened_date NULLS LAST, check_guid
            LIMIT 1
        ) oc ON true
        LEFT JOIN catering_details cd
            ON cd.order_guid = oh.order_guid
        LEFT JOIN dining_options do_
            ON do_.guid::text = oh.dining_option_guid::text
        WHERE oh.source = 'Catering'
          AND oh.voided = FALSE
          AND oh.estimated_fulfillment_date::date
              BETWEEN :start_date AND :end_date
          {location_filter}
          {dining_filter}
        ORDER BY
            l.location_name,
            oh.estimated_fulfillment_date
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    grouped = {}
    for row in rows:
        loc = row["location_name"] or "Unknown"
        if loc not in grouped:
            grouped[loc] = {"orders": [], "location_total": "$0.00"}

        order = dict(row)
        efd = order["estimated_fulfillment_date"]
        if efd and hasattr(efd, "strftime"):
            order["display_date"] = f"{efd.month}/{efd.day}"
            order["display_day"]  = efd.strftime("%a").upper()
        else:
            order["display_date"] = ""
            order["display_day"]  = ""

        order["display_total"] = _fmt(order.get("total_amount"))
        grouped[loc]["orders"].append(order)

    for loc, data in grouped.items():
        subtotal = sum(float(o.get("total_amount") or 0) for o in data["orders"])
        data["location_total"] = _fmt(subtotal)

    return grouped



def _get_orders(start_date, end_date, dining_option_guids=None):
    """
    Pull all catering orders in the date range, joined to location and
    catering_details. Returns rows grouped as:
        { route: { location_name: {"orders": [...], "location_total": "$x,xxx.xx"} } }
    Optional dining_option_guids (list) filters to specific dining options.
    """
    dining_filter = ""
    params = {"start_date": start_date, "end_date": end_date}
    if dining_option_guids:
        dining_filter = "AND oh.dining_option_guid::text = ANY(:dining_option_guids)"
        params["dining_option_guids"] = dining_option_guids

    sql = text(f"""
        SELECT
            oh.order_guid,
            oh.estimated_fulfillment_date,
            oh.closed_date,
            oh.location_id::text AS location_id,
            l.location_name,
            l.route,
            l.abbreviation,

            -- Customer name and total from first check
            UPPER(oc.customer_first) AS customer_first,
            UPPER(oc.customer_last)  AS customer_last,
            oc.total_amount,

            -- Catering detail fields — derive service_type from dining option if blank
            COALESCE(
                NULLIF(cd.service_type, ''),
                CASE
                    WHEN do_.name ILIKE '%delivery%' THEN 'DEL'
                    WHEN do_.name ILIKE '%pickup%'   THEN 'PICK UP'
                    ELSE NULL
                END
            ) AS service_type,
            cd.travel_time,
            cd.departure_time,
            cd.arrival_time,
            cd.duration,
            cd.return_time,
            cd.num_employees,
            cd.driver_assigned,
            cd.event_company,
            cd.client_type,
            cd.notes

        FROM orders_head oh
        JOIN locations l
            ON oh.location_id::text = l.store_guid::text
        LEFT JOIN LATERAL (
            SELECT customer_first, customer_last, total_amount
            FROM order_checks
            WHERE order_guid = oh.order_guid
            ORDER BY opened_date NULLS LAST, check_guid
            LIMIT 1
        ) oc ON true
        LEFT JOIN catering_details cd
            ON cd.order_guid = oh.order_guid
        LEFT JOIN dining_options do_
            ON do_.guid::text = oh.dining_option_guid::text
        WHERE oh.source = 'Catering'
          AND oh.voided = FALSE
          AND oh.estimated_fulfillment_date::date
              BETWEEN :start_date AND :end_date
          {dining_filter}
        ORDER BY
            l.route DESC,
            l.location_name,
            oh.estimated_fulfillment_date
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()

    grouped = {}

    for row in rows:
        route = (row["route"] or "Unassigned").title()
        loc   = row["location_name"] or "Unknown"

        if route not in grouped:
            grouped[route] = {}
        if loc not in grouped[route]:
            grouped[route][loc] = {"orders": [], "location_total": "$0.00"}

        order = dict(row)

        efd = order["estimated_fulfillment_date"]
        if efd and hasattr(efd, "strftime"):
            order["display_date"] = f"{efd.month}/{efd.day}"
            order["display_day"]  = efd.strftime("%a").upper()
        else:
            order["display_date"] = ""
            order["display_day"]  = ""

        order["display_total"] = _fmt(order.get("total_amount"))

        grouped[route][loc]["orders"].append(order)

    for route, locations in grouped.items():
        for loc, data in locations.items():
            subtotal = sum(float(o.get("total_amount") or 0) for o in data["orders"])
            data["location_total"] = _fmt(subtotal)

    return grouped


def _get_route_totals(grouped):
    """Compute per-route and grand total — all returned as formatted strings."""
    totals = {}
    grand  = 0.0
    for route, locations in grouped.items():
        rt = sum(
            float(o.get("total_amount") or 0)
            for data in locations.values()
            for o in data["orders"]
        )
        totals[route] = _fmt(rt)
        grand += rt
    totals["Grand Total"] = _fmt(grand)
    return totals


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("home.html", year=datetime.now().year)


@app.route("/admin")
@role_required("admin","catering")
def admin():
    return render_template("admin.html")


@app.route("/schedule")
@role_required("admin","catering")
def index():
    today     = date.today()
    start_str = request.args.get("start", today.strftime("%Y-%m-%d"))
    end_str   = request.args.get("end",   (today + timedelta(days=7)).strftime("%Y-%m-%d"))

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = today
        end_date   = today + timedelta(days=7)

    dining_options = _get_dining_options()
    client_types = _get_client_types()
    default_guid   = next((d["guid"] for d in dining_options if d["name"] == "Catering- Delivery"), "")
    selected_guids = request.args.getlist("dining_option")

    if not selected_guids or selected_guids == [""]:
        selected_guids = [default_guid] if default_guid else []

    if "" in selected_guids:
        selected_guids = []

    grouped = _get_orders(start_date, end_date, selected_guids or None)
    totals  = _get_route_totals(grouped)

    # Collect all order guids for notification check
    all_guids = [
        o["order_guid"]
        for locs in grouped.values()
        for data in locs.values()
        for o in data["orders"]
    ]
    unread = _get_unread_notifications(all_guids, g.user["username"])

    return render_template(
        "orders.html",
        grouped              = grouped,
        totals               = totals,
        start_date           = start_date.strftime("%Y-%m-%d"),
        end_date             = end_date.strftime("%Y-%m-%d"),
        today                = today.strftime("%Y-%m-%d"),
        dining_options       = dining_options,
        dining_option_guids  = selected_guids,
        client_types         = client_types,
        drivers_by_location  = _get_drivers_by_location(),
        unread_notifications = unread,
    )


@app.route("/save/<order_guid>", methods=["POST"])
@role_required("admin","catering","store","gm")
def save_order(order_guid):
    """Upsert catering_details for one order.
    Only updates fields that are present in the payload — missing fields
    are not overwritten, allowing partial saves (e.g. driver only).
    """
    data = request.get_json(force=True)

    def t(val):
        if not val or str(val).strip() == "":
            return None
        try:
            datetime.strptime(val.strip(), "%H:%M")
            return val.strip()
        except ValueError:
            return None

    def i(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    # All possible fields with their processed values
    all_fields = {
        "service_type":   data.get("service_type"),
        "travel_time":    data.get("travel_time"),
        "departure_time": t(data.get("departure_time")),
        "arrival_time":   t(data.get("arrival_time")),
        "duration":       data.get("duration"),
        "return_time":    t(data.get("return_time")),
        "num_employees":  i(data.get("num_employees")),
        "driver_assigned":data.get("driver_assigned"),
        "event_company":  data.get("event_company"),
        "client_type":    data.get("client_type"),
        "notes":          data.get("notes"),
    }

    # Only include fields that were explicitly sent in the payload
    sent_fields = {k: v for k, v in all_fields.items() if k in data}

    if not sent_fields:
        return jsonify({"status": "ok"})

    # Build dynamic UPDATE SET — only update what was sent
    update_set = ", ".join(f"{k} = EXCLUDED.{k}" for k in sent_fields)
    insert_cols = ", ".join(["order_guid"] + list(sent_fields.keys()) + ["last_updated"])
    insert_vals = ", ".join([":order_guid"] + [f":{k}" for k in sent_fields] + ["NOW()"])

    sql = text(f"""
        INSERT INTO catering_details ({insert_cols})
        VALUES ({insert_vals})
        ON CONFLICT (order_guid) DO UPDATE SET
            {update_set},
            last_updated = NOW()
    """)

    try:
        with engine.begin() as conn:
            params = {"order_guid": order_guid, **sent_fields}
            conn.execute(sql, params)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Save failed for {order_guid}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/schedule/print")
@role_required("admin","catering","store","gm")
def print_view():
    start_str      = request.args.get("start", date.today().strftime("%Y-%m-%d"))
    end_str        = request.args.get("end",   (date.today() + timedelta(days=7)).strftime("%Y-%m-%d"))
    route_filter   = request.args.get("route", "both").lower()
    selected_guids = request.args.getlist("dining_option")
    if "" in selected_guids:
        selected_guids = []

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = date.today()
        end_date   = date.today() + timedelta(days=7)

    grouped = _get_orders(start_date, end_date, selected_guids or None)

    if route_filter != "both":
        grouped = {
            route: locs
            for route, locs in grouped.items()
            if route.lower() == route_filter
        }

    totals = _get_route_totals(grouped)
    eastern = pytz.timezone("America/New_York")
    now_et  = datetime.now(pytz.utc).astimezone(eastern).strftime("%m/%d/%Y %I:%M %p ET")

    return render_template(
        "print.html",
        grouped             = grouped,
        totals              = totals,
        start_date          = start_date.strftime("%m/%d/%Y"),
        end_date            = end_date.strftime("%m/%d/%Y"),
        now                 = now_et,
        route_filter        = route_filter,
        dining_option_guids = selected_guids,
    )


@app.route("/map/<order_guid>")
@role_required("admin","catering","store","gm")
def map_view(order_guid):
    """
    Returns JSON with store + delivery coordinates and travel estimate.
    Geocodes delivery address on demand if coordinates are missing,
    then caches the result back to order_delivery_info.
    """
    sql = text("""
        SELECT
            oh.order_guid,
            oh.estimated_fulfillment_date,
            l.location_name,
            l.address   AS store_address,
            l.latitude  AS store_lat,
            l.longitude AS store_lon,
            di.address1,
            di.address2,
            di.city,
            di.state,
            di.zip_code,
            di.latitude  AS delivery_lat,
            di.longitude AS delivery_lon,
            oc.customer_first,
            oc.customer_last,
            oc.total_amount

        FROM orders_head oh
        JOIN locations l
            ON oh.location_id::text = l.store_guid::text
        LEFT JOIN order_delivery_info di
            ON di.order_guid = oh.order_guid
        LEFT JOIN LATERAL (
            SELECT customer_first, customer_last, total_amount
            FROM order_checks
            WHERE order_guid = oh.order_guid
            ORDER BY opened_date NULLS LAST, check_guid
            LIMIT 1
        ) oc ON true
        WHERE oh.order_guid = :order_guid
    """)

    with engine.connect() as conn:
        row = conn.execute(sql, {"order_guid": order_guid}).mappings().first()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    row = dict(row)

    parts = [row.get("address1"), row.get("address2"),
             row.get("city"), row.get("state"), row.get("zip_code")]
    delivery_address = ", ".join(p for p in parts if p)

    delivery_lat = row.get("delivery_lat")
    delivery_lon = row.get("delivery_lon")
    geocoded     = False

    if (delivery_lat is None or delivery_lon is None) and delivery_address:
        delivery_lat, delivery_lon = _geocode_address(delivery_address)
        if delivery_lat and delivery_lon:
            geocoded = True
            _cache_coordinates(order_guid, delivery_lat, delivery_lon)

    store_lat = row.get("store_lat")
    store_lon = row.get("store_lon")

    travel_minutes = None
    distance_miles = None
    route_geojson  = None

    if all(v is not None for v in [store_lat, store_lon, delivery_lat, delivery_lon]):
        route_geojson, distance_miles, travel_minutes = _get_ors_route(
            float(store_lat), float(store_lon),
            float(delivery_lat), float(delivery_lon)
        )
        if travel_minutes is None:
            logger.info(f"[MAP] Using Haversine fallback for order {order_guid}")
            print(f"[MAP] Using Haversine fallback for order {order_guid}")
            travel_minutes = _estimate_travel_minutes(
                float(store_lat), float(store_lon),
                float(delivery_lat), float(delivery_lon)
            )
            distance_miles = round(
                _haversine_miles(
                    float(store_lat), float(store_lon),
                    float(delivery_lat), float(delivery_lon)
                ) * 1.25, 1
            )
        else:
            logger.info(f"[MAP] Using ORS route for order {order_guid}")
            print(f"[MAP] Using ORS route for order {order_guid}")
    else:
        logger.warning(f"[MAP] Missing coordinates for order {order_guid}")

    efd = row.get("estimated_fulfillment_date")

    return jsonify({
        "order_guid":     order_guid,
        "display_date":   f"{efd.month}/{efd.day}" if efd else "",
        "customer":       f"{row.get('customer_first') or ''} {row.get('customer_last') or ''}".strip(),
        "total":          _fmt(row.get("total_amount")),
        "store": {
            "name":    row.get("location_name"),
            "address": row.get("store_address"),
            "lat":     float(store_lat) if store_lat else None,
            "lon":     float(store_lon) if store_lon else None,
        },
        "delivery": {
            "address":  delivery_address,
            "lat":      float(delivery_lat) if delivery_lat else None,
            "lon":      float(delivery_lon) if delivery_lon else None,
            "geocoded": geocoded,
        },
        "travel_minutes": travel_minutes,
        "distance_miles": distance_miles,
        "route_geojson":  route_geojson,
        "routed":         route_geojson is not None,
    })


@app.route("/store")
@role_required("admin","catering","store","gm")
def store():
    today     = date.today()
    start_str = request.args.get("start", today.strftime("%Y-%m-%d"))
    end_str   = request.args.get("end",   (today + timedelta(days=7)).strftime("%Y-%m-%d"))

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = today
        end_date   = today + timedelta(days=7)

    locations      = _get_store_locations_for_user(g.user)
    dining_options = _get_dining_options()

    # Default dining option to Catering- Delivery
    default_dining = next((d["guid"] for d in dining_options if d["name"] == "Catering- Delivery"), "")
    selected_dining_guids  = request.args.getlist("dining_option")

    if not selected_dining_guids or selected_dining_guids == [""]:
        selected_dining_guids = [default_dining] if default_dining else []
    if "" in selected_dining_guids:
        selected_dining_guids = []

    # For store role users — auto-apply their location, ignore URL params
    if "store" in g.user["roles"] and not any(r in g.user["roles"] for r in ["admin", "catering", "gm"]):
        selected_locations = [l["guid"] for l in locations]
    else:
        selected_locations = request.args.getlist("location")

    grouped = _get_store_orders(
        start_date, end_date,
        selected_locations or None,
        selected_dining_guids or None,
    )

    # Grand total
    grand_total = _fmt(sum(
        float(o.get("total_amount") or 0)
        for data in grouped.values()
        for o in data["orders"]
    ))

    # Collect all order guids for notification check
    all_guids = [o["order_guid"] for data in grouped.values() for o in data["orders"]]
    unread    = _get_unread_notifications(all_guids, g.user["username"])

    return render_template(
        "store.html",
        grouped               = grouped,
        grand_total           = grand_total,
        start_date            = start_date.strftime("%Y-%m-%d"),
        end_date              = end_date.strftime("%Y-%m-%d"),
        locations             = locations,
        selected_locations    = selected_locations,
        dining_options        = dining_options,
        dining_option_guids   = selected_dining_guids,
        drivers_by_location   = _get_drivers_by_location(),
        unread_notifications  = unread,
    )


@app.route("/store/print")
@role_required("admin","catering","store","gm")
def store_print():
    start_str = request.args.get("start", date.today().strftime("%Y-%m-%d"))
    end_str   = request.args.get("end",   (date.today() + timedelta(days=7)).strftime("%Y-%m-%d"))

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = date.today()
        end_date   = date.today() + timedelta(days=7)

    selected_locations    = request.args.getlist("location")
    selected_dining_guids = request.args.getlist("dining_option")
    if "" in selected_dining_guids:
        selected_dining_guids = []

    grouped = _get_store_orders(
        start_date, end_date,
        selected_locations or None,
        selected_dining_guids or None,
    )

    grand_total = _fmt(sum(
        float(o.get("total_amount") or 0)
        for data in grouped.values()
        for o in data["orders"]
    ))

    eastern = pytz.timezone("America/New_York")
    now_et  = datetime.now(pytz.utc).astimezone(eastern).strftime("%m/%d/%Y %I:%M %p ET")

    return render_template(
        "store_print.html",
        grouped            = grouped,
        grand_total        = grand_total,
        start_date         = start_date.strftime("%m/%d/%Y"),
        end_date           = end_date.strftime("%m/%d/%Y"),
        now                = now_et,
    )


###Drivers###
@app.route("/drivers")
@role_required("admin","catering")
def manage_drivers():
    """Render the driver management console."""
    # 1. Fetch all locations for both assignments AND profile dropdown tracking
    with engine.connect() as conn:
        loc_rows = conn.execute(text("""
            SELECT store_guid::text AS guid, location_name AS name, route
            FROM locations
            ORDER BY location_name
        """)).mappings().all()
        locations_list = [dict(r) for r in loc_rows]  # converted to plain dicts

    # 2. Fetch all drivers with their fields
    with engine.connect() as conn:
        drivers_rows = conn.execute(text("""
            SELECT id, full_name, first_name, last_name, nickname,
                   has_id, gender, dob, state, location, route, active,
                   has_auth, has_mvr, license_number, last_completed
            FROM catering_drivers
            ORDER BY active DESC, full_name ASC
        """)).mappings().all()

        # Convert RowMappings into JSON-serializable plain dictionaries
        # Dates must be converted to strings for tojson to work in the template
        drivers_list = []
        for r in drivers_rows:
            d = dict(r)
            d["dob"]            = to_date_str(d.get("dob"))
            d["last_completed"] = to_date_str(d.get("last_completed"))
            drivers_list.append(d)

        # 3. Fetch all current driver-location assignments
        assignment_rows = conn.execute(text("""
            SELECT driver_id, store_guid::text AS store_guid 
            FROM catering_driver_locations
        """)).mappings().all()

    assignments = {}
    for row in assignment_rows:
        assignments.setdefault(row["driver_id"], []).append(row["store_guid"])

    return render_template(
        "drivers.html",
        drivers=drivers_list,  # Pass the serializable list of plain dicts
        locations=locations_list,
        assignments=assignments
    )


@app.route("/drivers/save", methods=["POST"])
@role_required("admin","catering")
def save_driver():
    """Create or update a driver profile."""
    data = request.get_json(force=True)
    driver_id = data.get("id")

    def parse_date(val):
        if not val or str(val).strip() == "": return None
        try:
            return datetime.strptime(val.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    params = {
        "full_name": data.get("full_name"),
        "first_name": data.get("first_name") or None,
        "last_name": data.get("last_name") or None,
        "nickname": data.get("nickname") or None,
        "has_id": bool(data.get("has_id")),
        "gender": data.get("gender") or None,
        "dob": parse_date(data.get("dob")),
        "state": data.get("state") or None,
        "location": data.get("location") or None,
        "route": data.get("route") or None,
        "active": bool(data.get("active", True)),
        "has_auth": bool(data.get("has_auth")),
        "has_mvr": bool(data.get("has_mvr")),
        "license_number": data.get("license_number") or None,
        "last_completed": parse_date(data.get("last_completed"))
    }

    if driver_id:
        # Update existing driver
        params["id"] = int(driver_id)
        sql = text("""
            UPDATE catering_drivers SET
                full_name = :full_name, first_name = :first_name, last_name = :last_name, nickname = :nickname,
                has_id = :has_id, gender = :gender, dob = :dob, state = :state, location = :location,
                route = :route, active = :active, has_auth = :has_auth, has_mvr = :has_mvr,
                license_number = :license_number, last_completed = :last_completed
            WHERE id = :id
        """)
    else:
        # Create new driver
        sql = text("""
            INSERT INTO catering_drivers (
                full_name, first_name, last_name, nickname, has_id, gender, dob, state,
                location, route, active, has_auth, has_mvr, license_number, last_completed
            ) VALUES (
                :full_name, :first_name, :last_name, :nickname, :has_id, :gender, :dob, :state,
                :location, :route, :active, :has_auth, :has_mvr, :license_number, :last_completed
            ) RETURNING id
        """)

    try:
        with engine.begin() as conn:
            result = conn.execute(sql, params)
            if not driver_id:
                driver_id = result.scalar()
        return jsonify({"status": "ok", "driver_id": driver_id})
    except Exception as e:
        logger.error(f"Failed to save driver: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/drivers/save_locations", methods=["POST"])
@role_required("admin","catering")
def save_driver_locations():
    """Sync locations assigned to a driver."""
    data = request.get_json(force=True)
    driver_id = data.get("driver_id")
    store_guids = data.get("store_guids", [])  # List of UUID strings

    if not driver_id:
        return jsonify({"status": "error", "message": "Missing driver_id"}), 400

    try:
        with engine.begin() as conn:
            # Clear old locations
            conn.execute(text("DELETE FROM catering_driver_locations WHERE driver_id = :id"), {"id": driver_id})
            # Insert new selections
            if store_guids:
                ins_sql = text("INSERT INTO catering_driver_locations (driver_id, store_guid) VALUES (:id, :guid)")
                for guid in store_guids:
                    conn.execute(ins_sql, {"id": driver_id, "guid": guid})
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Failed to save driver locations: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/drivers/print")
@role_required("admin","catering")
def print_drivers():
    include_inactive = request.args.get("inactive", "false").lower() == "true"

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                cd.full_name,
                cd.nickname,
                cd.location,
                cd.has_id,
                cd.has_auth,
                cd.has_mvr,
                cd.last_completed,
                cd.active
            FROM catering_drivers cd
            WHERE cd.active = TRUE OR :include_inactive
            ORDER BY cd.location, cd.full_name
        """), {"include_inactive": include_inactive}).mappings().all()

    # Group by location
    grouped = {}
    for row in rows:
        loc = row["location"] or "Unassigned"
        if loc not in grouped:
            grouped[loc] = []
        d = dict(row)
        d["last_completed"] = to_date_str(d.get("last_completed"))
        grouped[loc].append(d)

    eastern = pytz.timezone("America/New_York")
    now_et  = datetime.now(pytz.utc).astimezone(eastern).strftime("%m/%d/%Y %I:%M %p ET")

    return render_template(
        "drivers_print.html",
        grouped          = grouped,
        include_inactive = include_inactive,
        now              = now_et,
    )




# ── SharePoint / Graph API helpers ────────────────────────────────────────────

def _get_graph_token():
    """Get an app-level OAuth token from Microsoft for Graph API calls."""
    resp = http_requests.post(
        f"https://login.microsoftonline.com/{SP_TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     SP_CLIENT_ID,
            "client_secret": SP_CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default",
        }
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _sp_upload(order_guid, filename, file_bytes):
    """
    Upload a file to SharePoint under Catering Attachments/{order_guid}/filename.
    Uses a unique prefix on the SharePoint filename to prevent path collisions.
    Returns the Graph API item ID.
    """
    import uuid
    token   = _get_graph_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}

    # Unique name in SharePoint to prevent cross-order collisions
    unique_prefix = uuid.uuid4().hex[:8]
    sp_name       = f"{unique_prefix}_{filename}"
    path          = f"Catering Attachments/{order_guid}/{sp_name}"
    url           = f"https://graph.microsoft.com/v1.0/drives/{SP_DRIVE_ID}/root:/{path}:/content"

    resp = http_requests.put(url, headers=headers, data=file_bytes)
    resp.raise_for_status()
    return resp.json()["id"]


def _sp_download(sharepoint_id):
    """
    Download a file from SharePoint by item ID.
    Returns (filename, content_bytes, mime_type).
    """
    token   = _get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Get file metadata first
    meta_url  = f"https://graph.microsoft.com/v1.0/drives/{SP_DRIVE_ID}/items/{sharepoint_id}"
    meta_resp = http_requests.get(meta_url, headers=headers)
    meta_resp.raise_for_status()
    meta      = meta_resp.json()
    filename  = meta.get("name", "attachment")
    mime      = meta.get("file", {}).get("mimeType", "application/octet-stream")

    # Download content
    dl_url    = meta["@microsoft.graph.downloadUrl"]
    dl_resp   = http_requests.get(dl_url)
    dl_resp.raise_for_status()
    return filename, dl_resp.content, mime


def _sp_delete(sharepoint_id):
    """Delete a file from SharePoint by item ID."""
    token   = _get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}
    url     = f"https://graph.microsoft.com/v1.0/drives/{SP_DRIVE_ID}/items/{sharepoint_id}"
    resp    = http_requests.delete(url, headers=headers)
    resp.raise_for_status()


# ── Attachment DB helpers ─────────────────────────────────────────────────────

def _get_attachments(order_guid):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, filename, uploaded_by, uploaded_at
            FROM order_attachments
            WHERE order_guid = :order_guid
            ORDER BY uploaded_at DESC
        """), {"order_guid": order_guid}).mappings().all()
    return [dict(r) for r in rows]


def _get_unread_notifications(order_guids, user_email):
    """
    Returns set of order_guids that have unread notifications for this user.
    """
    if not order_guids:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT order_guid
            FROM order_notifications
            WHERE order_guid = ANY(:guids)
              AND NOT (:email = ANY(seen_by))
        """), {"guids": list(order_guids), "email": user_email}).mappings().all()
    return {r["order_guid"] for r in rows}


def _create_notification(order_guid, change_type):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO order_notifications (order_guid, change_type, changed_by)
            VALUES (:order_guid, :change_type, :changed_by)
        """), {
            "order_guid":  order_guid,
            "change_type": change_type,
            "changed_by":  g.user["username"],
        })


def _mark_seen(order_guid):
    email = g.user["username"]
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE order_notifications
            SET seen_by = array_append(seen_by, :email)
            WHERE order_guid = :order_guid
              AND NOT (:email = ANY(seen_by))
        """), {"order_guid": order_guid, "email": email})


# ── Attachment routes ─────────────────────────────────────────────────────────

@app.route("/attachments/<order_guid>")
@role_required("admin", "catering", "gm", "store")
def list_attachments(order_guid):
    attachments = _get_attachments(order_guid)
    # Serialize dates
    for a in attachments:
        if a.get("uploaded_at"):
            a["uploaded_at"] = a["uploaded_at"].strftime("%m/%d/%Y %I:%M %p")
    return jsonify({"attachments": attachments})


@app.route("/attachments/<order_guid>/upload", methods=["POST"])
@role_required("admin", "catering")
def upload_attachment(order_guid):
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"status": "error", "message": "No filename"}), 400

    # Check allowed types — PDF only
    allowed = {".pdf"}
    ext     = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed:
        return jsonify({"status": "error", "message": "Only PDF files are allowed"}), 400

    # Check file size
    file_bytes = f.read()
    if len(file_bytes) > SP_MAX_BYTES:
        return jsonify({"status": "error", "message": "File exceeds 25MB limit"}), 400

    # Check for duplicate filename on this order
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT id FROM order_attachments
            WHERE order_guid = :order_guid AND filename = :filename
        """), {"order_guid": order_guid, "filename": f.filename}).first()

    if existing:
        return jsonify({
            "status":  "error",
            "message": f'A file named "{f.filename}" is already attached to this order.'
        }), 400

    try:
        sp_id = _sp_upload(order_guid, f.filename, file_bytes)
    except Exception as e:
        logger.error(f"SharePoint upload failed: {e}")
        return jsonify({"status": "error", "message": "Upload to SharePoint failed"}), 500

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO order_attachments (order_guid, filename, sharepoint_id, uploaded_by)
            VALUES (:order_guid, :filename, :sharepoint_id, :uploaded_by)
        """), {
            "order_guid":    order_guid,
            "filename":      f.filename,
            "sharepoint_id": sp_id,
            "uploaded_by":   g.user["username"],
        })

    _create_notification(order_guid, "file_attached")
    return jsonify({"status": "ok"})


@app.route("/attachments/<int:attachment_id>/download")
@role_required("admin", "catering", "gm", "store")
def download_attachment(attachment_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT filename, sharepoint_id
            FROM order_attachments WHERE id = :id
        """), {"id": attachment_id}).mappings().first()

    if not row:
        abort(404)

    try:
        filename, content, mime = _sp_download(row["sharepoint_id"])
    except Exception as e:
        logger.error(f"SharePoint download failed: {e}")
        abort(500)

    return Response(
        content,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'}
    )


@app.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
@role_required("admin", "catering")
def delete_attachment(attachment_id):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT order_guid, sharepoint_id
            FROM order_attachments WHERE id = :id
        """), {"id": attachment_id}).mappings().first()

    if not row:
        abort(404)

    try:
        _sp_delete(row["sharepoint_id"])
    except Exception as e:
        logger.error(f"SharePoint delete failed: {e}")
        return jsonify({"status": "error", "message": "Failed to delete from SharePoint"}), 500

    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM order_attachments WHERE id = :id"
        ), {"id": attachment_id})

    _create_notification(row["order_guid"], "file_deleted")
    return jsonify({"status": "ok"})


# ── Notification routes ───────────────────────────────────────────────────────

@app.route("/notifications/<order_guid>/seen", methods=["POST"])
@role_required("admin", "catering", "gm", "store")
def mark_notification_seen(order_guid):
    _mark_seen(order_guid)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
