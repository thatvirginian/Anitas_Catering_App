# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, jsonify
from sqlalchemy import text
from datetime import datetime, date, timedelta
import logging
import math
import os
import requests as http_requests

from src.database_setup import get_engine

app = Flask(__name__)
logger = logging.getLogger(__name__)
engine = get_engine()

GEOCODIO_API_KEY = os.getenv("GEOCODIO_API_KEY")
ORS_API_KEY      = os.getenv("ORS_API_KEY")


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
        logger.info(f"[ORS] Requesting route: {store_lat},{store_lon} → {delivery_lat},{delivery_lon}")
        print(f"[ORS] Requesting route: {store_lat},{store_lon} → {delivery_lat},{delivery_lon}")

        resp = http_requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"[ORS] Raw response keys: {list(data.keys())}")

        features = data.get("features", [])
        if features:
            feature  = features[0]
            props    = feature["properties"]["summary"]
            geojson  = feature["geometry"]
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
            ORDER BY name
        """)).mappings().all()
    return [dict(r) for r in rows]


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
            l.location_name,
            l.route,
            l.abbreviation,

            -- Customer name and total from first check
            UPPER(oc.customer_first) AS customer_first,
            UPPER(oc.customer_last) AS customer_last,
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
            cd.notes

        FROM orders_head oh
        JOIN locations l
            ON oh.location_id::text = l.store_guid::text
        LEFT JOIN LATERAL (
            SELECT customer_first, customer_last, total_amount
            FROM order_checks
            WHERE order_guid = oh.order_guid
            ORDER BY opened_date
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

    # Group: route → location → {orders, location_total}
    grouped = {}

    for row in rows:
        route = (row["route"] or "Unassigned").title()
        loc   = row["location_name"] or "Unknown"

        if route not in grouped:
            grouped[route] = {}
        if loc not in grouped[route]:
            grouped[route][loc] = {"orders": [], "location_total": "$0.00"}

        order = dict(row)

        # Format dates for display
        efd = order["estimated_fulfillment_date"]
        if efd and hasattr(efd, "strftime"):
            order["display_date"] = f"{efd.month}/{efd.day}"
            order["display_day"]  = efd.strftime("%a").upper()
        else:
            order["display_date"] = ""
            order["display_day"]  = ""

        # Format order total
        order["display_total"] = _fmt(order.get("total_amount"))

        grouped[route][loc]["orders"].append(order)

    # Compute location totals now that all orders are grouped
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

    dining_options     = _get_dining_options()
    # Default to Catering - Delivery guid if no selection made
    default_guid       = next((d["guid"] for d in dining_options if d["name"] == "Catering- Delivery"), "")
    selected_guids     = request.args.getlist("dining_option")

    # If nothing selected, default to Catering - Delivery
    if not selected_guids or selected_guids == [""]:
        selected_guids = [default_guid] if default_guid else []

    # Empty string means ALL was selected
    if "" in selected_guids:
        selected_guids = []

    grouped = _get_orders(start_date, end_date, selected_guids or None)
    totals  = _get_route_totals(grouped)

    return render_template(
        "orders.html",
        grouped             = grouped,
        totals              = totals,
        start_date          = start_date.strftime("%Y-%m-%d"),
        end_date            = end_date.strftime("%Y-%m-%d"),
        today               = today.strftime("%Y-%m-%d"),
        dining_options      = dining_options,
        dining_option_guids = selected_guids,
    )


@app.route("/save/<order_guid>", methods=["POST"])
def save_order(order_guid):
    """Upsert catering_details for one order."""
    data = request.get_json(force=True)

    def t(val):
        """Parse HH:MM time string or return None."""
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

    sql = text("""
        INSERT INTO catering_details (
            order_guid, service_type, travel_time, departure_time,
            arrival_time, duration, return_time, num_employees,
            driver_assigned, event_company, notes, last_updated
        ) VALUES (
            :order_guid, :service_type, :travel_time, :departure_time,
            :arrival_time, :duration, :return_time, :num_employees,
            :driver_assigned, :event_company, :notes, NOW()
        )
        ON CONFLICT (order_guid) DO UPDATE SET
            service_type    = EXCLUDED.service_type,
            travel_time     = EXCLUDED.travel_time,
            departure_time  = EXCLUDED.departure_time,
            arrival_time    = EXCLUDED.arrival_time,
            duration        = EXCLUDED.duration,
            return_time     = EXCLUDED.return_time,
            num_employees   = EXCLUDED.num_employees,
            driver_assigned = EXCLUDED.driver_assigned,
            event_company   = EXCLUDED.event_company,
            notes           = EXCLUDED.notes,
            last_updated    = NOW()
    """)

    try:
        with engine.begin() as conn:
            conn.execute(sql, {
                "order_guid":     order_guid,
                "service_type":   data.get("service_type"),
                "travel_time":    data.get("travel_time"),
                "departure_time": t(data.get("departure_time")),
                "arrival_time":   t(data.get("arrival_time")),
                "duration":       data.get("duration"),
                "return_time":    t(data.get("return_time")),
                "num_employees":  i(data.get("num_employees")),
                "driver_assigned":data.get("driver_assigned"),
                "event_company":  data.get("event_company"),
                "notes":          data.get("notes"),
            })
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Save failed for {order_guid}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/print")
def print_view():
    start_str          = request.args.get("start", date.today().strftime("%Y-%m-%d"))
    end_str            = request.args.get("end",   (date.today() + timedelta(days=7)).strftime("%Y-%m-%d"))
    route_filter       = request.args.get("route", "both").lower()
    selected_guids     = request.args.getlist("dining_option")
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

    return render_template(
        "print.html",
        grouped            = grouped,
        totals             = totals,
        start_date         = start_date.strftime("%m/%d/%Y"),
        end_date           = end_date.strftime("%m/%d/%Y"),
        now                = datetime.now().strftime("%m/%d/%Y %I:%M %p"),
        route_filter       = route_filter,
        dining_option_guids = selected_guids,
    )


@app.route("/map/<order_guid>")
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

            -- Store coordinates
            l.location_name,
            l.address   AS store_address,
            l.latitude  AS store_lat,
            l.longitude AS store_lon,

            -- Delivery info
            di.address1,
            di.address2,
            di.city,
            di.state,
            di.zip_code,
            di.latitude  AS delivery_lat,
            di.longitude AS delivery_lon,

            -- Customer
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
            ORDER BY opened_date LIMIT 1
        ) oc ON true
        WHERE oh.order_guid = :order_guid
    """)

    with engine.connect() as conn:
        row = conn.execute(sql, {"order_guid": order_guid}).mappings().first()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    row = dict(row)

    # Build delivery address string for geocoding fallback
    parts = [row.get("address1"), row.get("address2"),
             row.get("city"), row.get("state"), row.get("zip_code")]
    delivery_address = ", ".join(p for p in parts if p)

    delivery_lat = row.get("delivery_lat")
    delivery_lon = row.get("delivery_lon")
    geocoded     = False

    # Geocode on demand if coordinates are missing
    if (delivery_lat is None or delivery_lon is None) and delivery_address:
        delivery_lat, delivery_lon = _geocode_address(delivery_address)
        if delivery_lat and delivery_lon:
            geocoded = True
            _cache_coordinates(order_guid, delivery_lat, delivery_lon)

    store_lat = row.get("store_lat")
    store_lon = row.get("store_lon")

    # Calculate travel estimate
    travel_minutes = None
    distance_miles = None
    route_geojson  = None

    if all(v is not None for v in [store_lat, store_lon, delivery_lat, delivery_lon]):
        # Try OSRM for real driving route first
        route_geojson, distance_miles, travel_minutes = _get_ors_route(
            float(store_lat), float(store_lon),
            float(delivery_lat), float(delivery_lon)
        )
        # Fall back to Haversine if OSRM fails
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
            logger.info(f"[MAP] Using OSRM route for order {order_guid}")
            print(f"[MAP] Using OSRM route for order {order_guid}")
    else:
        logger.warning(f"[MAP] Missing coordinates for order {order_guid} — store: ({store_lat},{store_lon}) delivery: ({delivery_lat},{delivery_lon})")

    efd = row.get("estimated_fulfillment_date")

    return jsonify({
        "order_guid":       order_guid,
        "display_date":     f"{efd.month}/{efd.day}" if efd else "",
        "customer":         f"{row.get('customer_first') or ''} {row.get('customer_last') or ''}".strip(),
        "total":            _fmt(row.get("total_amount")),

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


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
