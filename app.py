from flask import Flask, request, redirect, url_for, render_template_string
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

APP_NAME = "Restaurant Intelligence"
DB_PATH = Path(__file__).with_name("restaurant.db")

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            selling_price REAL NOT NULL CHECK (selling_price >= 0),
            food_cost REAL NOT NULL CHECK (food_cost >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date TEXT NOT NULL,
            menu_item_id INTEGER NOT NULL,
            quantity_sold INTEGER NOT NULL CHECK (quantity_sold >= 0),
            wasted_portions INTEGER NOT NULL DEFAULT 0 CHECK (wasted_portions >= 0),
            FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
        );
        """
    )
    conn.commit()
    conn.close()


def money(value):
    return f"{value:,.2f}"


def forecast_for_item(conn, item_id):
    """Transparent forecasting baseline:
    60% weight to the most recent 7 recorded days,
    40% weight to the previous 7 recorded days.
    A small safety buffer is added to produce prep quantity.
    """
    rows = conn.execute(
        """
        SELECT sale_date, quantity_sold
        FROM sales
        WHERE menu_item_id = ?
        ORDER BY sale_date DESC, id DESC
        LIMIT 14
        """,
        (item_id,),
    ).fetchall()

    if not rows:
        return {"forecast": 0, "prepare": 0, "confidence": "No data"}

    recent = [r["quantity_sold"] for r in rows[:7]]
    previous = [r["quantity_sold"] for r in rows[7:14]]

    recent_avg = sum(recent) / len(recent)
    if previous:
        previous_avg = sum(previous) / len(previous)
        forecast = (0.60 * recent_avg) + (0.40 * previous_avg)
    else:
        forecast = recent_avg

    forecast = max(0, round(forecast))
    prepare = max(0, round(forecast * 1.08))

    if len(rows) >= 14:
        confidence = "Good"
    elif len(rows) >= 7:
        confidence = "Developing"
    else:
        confidence = "Early"

    return {"forecast": forecast, "prepare": prepare, "confidence": confidence}


@app.route("/")
def dashboard():
    conn = get_db()

    items = conn.execute(
        "SELECT * FROM menu_items ORDER BY name"
    ).fetchall()

    today = datetime.now().date().isoformat()
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

    summary = conn.execute(
        """
        SELECT
            COALESCE(SUM(s.quantity_sold * m.selling_price), 0) AS revenue,
            COALESCE(SUM(s.quantity_sold * m.food_cost), 0) AS food_cost,
            COALESCE(SUM(s.wasted_portions * m.food_cost), 0) AS waste_cost
        FROM sales s
        JOIN menu_items m ON m.id = s.menu_item_id
        WHERE s.sale_date = ?
        """,
        (today,),
    ).fetchone()

    yesterday_summary = conn.execute(
        """
        SELECT
            COALESCE(SUM(s.quantity_sold * m.selling_price), 0) AS revenue
        FROM sales s
        JOIN menu_items m ON m.id = s.menu_item_id
        WHERE s.sale_date = ?
        """,
        (yesterday,),
    ).fetchone()

    forecasts = []
    for item in items:
        f = forecast_for_item(conn, item["id"])
        gross_profit_per_portion = item["selling_price"] - item["food_cost"]
        forecasts.append(
            {
                "id": item["id"],
                "name": item["name"],
                "selling_price": item["selling_price"],
                "food_cost": item["food_cost"],
                "margin": gross_profit_per_portion,
                **f,
            }
        )

    recent_sales = conn.execute(
        """
        SELECT s.sale_date, m.name, s.quantity_sold, s.wasted_portions
        FROM sales s
        JOIN menu_items m ON m.id = s.menu_item_id
        ORDER BY s.sale_date DESC, s.id DESC
        LIMIT 20
        """
    ).fetchall()

    revenue = summary["revenue"]
    food_cost = summary["food_cost"]
    waste_cost = summary["waste_cost"]
    gross_profit = revenue - food_cost - waste_cost

    conn.close()

    return render_template_string(
        TEMPLATE,
        app_name=APP_NAME,
        items=items,
        forecasts=forecasts,
        recent_sales=recent_sales,
        today=today,
        revenue=money(revenue),
        gross_profit=money(gross_profit),
        waste_cost=money(waste_cost),
        yesterday_revenue=money(yesterday_summary["revenue"]),
    )


@app.route("/menu/add", methods=["POST"])
def add_menu_item():
    name = request.form.get("name", "").strip()
    selling_price = request.form.get("selling_price", "0").strip()
    food_cost = request.form.get("food_cost", "0").strip()

    if not name:
        return redirect(url_for("dashboard"))

    try:
        selling_price = float(selling_price)
        food_cost = float(food_cost)
    except ValueError:
        return redirect(url_for("dashboard"))

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO menu_items (name, selling_price, food_cost)
            VALUES (?, ?, ?)
            """,
            (name, selling_price, food_cost),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

    return redirect(url_for("dashboard"))


@app.route("/sales/add", methods=["POST"])
def add_sale():
    sale_date = request.form.get("sale_date", "").strip()
    menu_item_id = request.form.get("menu_item_id", "").strip()
    quantity_sold = request.form.get("quantity_sold", "0").strip()
    wasted_portions = request.form.get("wasted_portions", "0").strip()

    try:
        datetime.strptime(sale_date, "%Y-%m-%d")
        menu_item_id = int(menu_item_id)
        quantity_sold = max(0, int(quantity_sold))
        wasted_portions = max(0, int(wasted_portions))
    except (ValueError, TypeError):
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute(
        """
        INSERT INTO sales (sale_date, menu_item_id, quantity_sold, wasted_portions)
        VALUES (?, ?, ?, ?)
        """,
        (sale_date, menu_item_id, quantity_sold, wasted_portions),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ app_name }}</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #697386;
      --border: #e5e9f2;
      --accent: #172033;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 20px; }
    .topbar {
      display: flex; align-items: center; justify-content: space-between;
      gap: 12px; margin-bottom: 20px;
    }
    h1 { font-size: 26px; margin: 0; }
    h2 { font-size: 18px; margin: 0 0 14px; }
    .small { color: var(--muted); font-size: 13px; }
    .cards {
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 12px; margin-bottom: 16px;
    }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 14px; padding: 16px;
    }
    .metric { font-size: 24px; font-weight: 750; margin-top: 6px; }
    .grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
      margin-bottom: 16px;
    }
    form { display: grid; gap: 10px; }
    input, select, button {
      width: 100%; border-radius: 10px; padding: 11px 12px;
      border: 1px solid var(--border); font: inherit;
    }
    button {
      background: var(--accent); color: white; font-weight: 700;
      cursor: pointer;
    }
    table { width: 100%; border-collapse: collapse; }
    th, td {
      text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border);
      font-size: 14px;
    }
    th { color: var(--muted); font-weight: 650; }
    .table-wrap { overflow-x: auto; }
    .pill {
      display: inline-block; padding: 4px 8px; border-radius: 999px;
      border: 1px solid var(--border); font-size: 12px;
    }
    @media (max-width: 800px) {
      .cards { grid-template-columns: 1fr 1fr; }
      .grid { grid-template-columns: 1fr; }
      .wrap { padding: 14px; }
      h1 { font-size: 22px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>{{ app_name }}</h1>
        <div class="small">Demand, waste and profitability dashboard</div>
      </div>
      <div class="small">{{ today }}</div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="small">Today's revenue</div>
        <div class="metric">{{ revenue }}</div>
      </div>
      <div class="card">
        <div class="small">Gross profit</div>
        <div class="metric">{{ gross_profit }}</div>
      </div>
      <div class="card">
        <div class="small">Waste cost</div>
        <div class="metric">{{ waste_cost }}</div>
      </div>
      <div class="card">
        <div class="small">Yesterday revenue</div>
        <div class="metric">{{ yesterday_revenue }}</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Add menu item</h2>
        <form method="post" action="/menu/add">
          <input name="name" placeholder="Dish name" required>
          <input name="selling_price" type="number" step="0.01" min="0" placeholder="Selling price" required>
          <input name="food_cost" type="number" step="0.01" min="0" placeholder="Food cost per portion" required>
          <button type="submit">Save menu item</button>
        </form>
      </div>

      <div class="card">
        <h2>Add daily sale</h2>
        <form method="post" action="/sales/add">
          <input name="sale_date" type="date" value="{{ today }}" required>
          <select name="menu_item_id" required>
            <option value="">Choose menu item</option>
            {% for item in items %}
              <option value="{{ item.id }}">{{ item.name }}</option>
            {% endfor %}
          </select>
          <input name="quantity_sold" type="number" min="0" placeholder="Quantity sold" required>
          <input name="wasted_portions" type="number" min="0" value="0" placeholder="Wasted portions">
          <button type="submit">Save sales</button>
        </form>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <h2>Tomorrow's preparation recommendations</h2>
      <div class="small" style="margin-bottom:10px;">
        Forecast is based on recent recorded sales. More history improves reliability.
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Dish</th>
              <th>Forecast</th>
              <th>Prepare</th>
              <th>Profit/portion</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {% for row in forecasts %}
            <tr>
              <td>{{ row.name }}</td>
              <td>{{ row.forecast }}</td>
              <td><strong>{{ row.prepare }}</strong></td>
              <td>{{ "%.2f"|format(row.margin) }}</td>
              <td><span class="pill">{{ row.confidence }}</span></td>
            </tr>
            {% else %}
            <tr><td colspan="5">Add menu items and sales to generate forecasts.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2>Recent sales</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Dish</th><th>Sold</th><th>Waste</th></tr>
          </thead>
          <tbody>
            {% for row in recent_sales %}
              <tr>
                <td>{{ row.sale_date }}</td>
                <td>{{ row.name }}</td>
                <td>{{ row.quantity_sold }}</td>
                <td>{{ row.wasted_portions }}</td>
              </tr>
            {% else %}
              <tr><td colspan="4">No sales recorded yet.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
