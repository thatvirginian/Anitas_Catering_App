# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, jsonify
from sqlalchemy import text
from datetime import datetime, date, timedelta
import logging

from src.database_setup import get_engine

app = Flask(__name__)
logger = logging.getLogger(__name__)
engine = get_engine()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(value):
    """Format a numeric value as a currency string with commas."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _get_orders(start_date, end_date):
    """
    Pull all catering orders in the date range, joined to location and
    catering_details. Returns rows grouped as:
        { route: { location_name: {"orders": [...], "location_total": "$x,xxx.xx"} } }
    """
    sql = text("""
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

            -- Catering detail fields (all nullable — LEFT JOIN)
            cd.service_type,
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
        WHERE oh.source IN ('Catering', 'Invoice')
          AND oh.voided = FALSE
          AND oh.estimated_fulfillment_date::date
              BETWEEN :start_date AND :end_date
        ORDER BY
            l.route DESC,
            l.location_name,
            oh.estimated_fulfillment_date
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "start_date": start_date,
            "end_date":   end_date,
        }).mappings().all()

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
    # Default: today through +7 days
    today     = date.today()
    start_str = request.args.get("start", today.strftime("%Y-%m-%d"))
    end_str   = request.args.get("end",   (today + timedelta(days=7)).strftime("%Y-%m-%d"))

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = today
        end_date   = today + timedelta(days=7)

    grouped = _get_orders(start_date, end_date)
    totals  = _get_route_totals(grouped)

    return render_template(
        "orders.html",
        grouped    = grouped,
        totals     = totals,
        start_date = start_date.strftime("%Y-%m-%d"),
        end_date   = end_date.strftime("%Y-%m-%d"),
        today      = today.strftime("%Y-%m-%d"),
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
    start_str  = request.args.get("start", date.today().strftime("%Y-%m-%d"))
    end_str    = request.args.get("end",   (date.today() + timedelta(days=7)).strftime("%Y-%m-%d"))
    route_filter = request.args.get("route", "both").lower()

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    except ValueError:
        start_date = date.today()
        end_date   = date.today() + timedelta(days=7)

    grouped = _get_orders(start_date, end_date)

    # Filter routes if not showing both
    if route_filter != "both":
        grouped = {
            route: locs
            for route, locs in grouped.items()
            if route.lower() == route_filter
        }

    totals  = _get_route_totals(grouped)

    return render_template(
        "print.html",
        grouped    = grouped,
        totals     = totals,
        start_date = start_date.strftime("%m/%d/%Y"),
        end_date   = end_date.strftime("%m/%d/%Y"),
        now        = datetime.now().strftime("%m/%d/%Y %I:%M %p"),
        route_filter = route_filter,
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
