"""
Generates mock Shopify-style data for the BigQuery Bronze layer.

Design notes:
- Two tenants with an uneven split (brand_a larger) so tenant isolation bugs are visible.
- Orders follow weekly seasonality (weekend lift) and recent-weeks growth, so the
  time-series charts have shape and the dbt date-based models are testable.
- A minority of customers are "repeat buyers" generating multiple orders. This
  makes new-vs-returning and LTV distributions realistic (power-law-ish).
- Refunds are generated for ~7% of orders, 3-14 days after the order date.
- Output files are written next to this script, regardless of the working
  directory the script is invoked from.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

fake = Faker("en_GB")
Faker.seed(42)
random.seed(42)

# --- Config ---------------------------------------------------------------

TENANTS = ["brand_a", "brand_b"]
TENANT_WEIGHTS = [0.7, 0.3]  # brand_a is the larger tenant

NUM_CUSTOMERS = 1200
NUM_PRODUCTS = 30
NUM_ORDERS = 2000
REFUND_RATE = 0.07

DAYS_OF_HISTORY = 365

# Realistic-ish product names per category, to replace Faker's catch_phrase noise.
PRODUCT_NAMES = {
    "Apparel": [
        "Merino Crew Neck", "Oversized Cotton Tee", "Linen Shirt",
        "Cashmere Cardigan", "Striped Breton Top", "Tailored Wool Blazer",
        "Relaxed Chino Trousers", "Denim Jacket",
    ],
    "Accessories": [
        "Leather Card Holder", "Canvas Tote", "Silk Scarf",
        "Woven Belt", "Minimalist Watch", "Wool Beanie",
    ],
    "Footwear": [
        "Suede Loafers", "Leather Chelsea Boots", "Canvas Trainers",
        "Running Shoe v2", "Hiking Boot", "Leather Sandals",
    ],
    "Home Goods": [
        "Ceramic Mug Set", "Linen Bedding", "Scented Candle",
        "Wool Throw", "Glass Carafe", "Bamboo Cutting Board",
    ],
}

UTM_WEIGHTS = {
    "google": 0.30,
    "facebook": 0.25,
    "organic": 0.20,
    "email": 0.15,
    "tiktok": 0.10,
}

# --- Generators -----------------------------------------------------------

def pick_tenant() -> str:
    return random.choices(TENANTS, weights=TENANT_WEIGHTS, k=1)[0]


def generate_customers() -> pd.DataFrame:
    customers = []
    for _ in range(NUM_CUSTOMERS):
        customers.append({
            "customer_id": fake.unique.uuid4(),
            "tenant_id": pick_tenant(),
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "email": fake.email(),
            "city": fake.city(),
            "created_at": fake.date_time_between(
                start_date="-2y", end_date="now"
            ).isoformat(),
        })
    return pd.DataFrame(customers)


def generate_products() -> pd.DataFrame:
    products = []
    for _ in range(NUM_PRODUCTS):
        category = random.choice(list(PRODUCT_NAMES.keys()))
        products.append({
            "product_id": fake.unique.uuid4(),
            "tenant_id": pick_tenant(),
            "sku": fake.bothify(text="SKU-####-???").upper(),
            "product_name": random.choice(PRODUCT_NAMES[category]),
            "category": category,
            "price": round(random.uniform(15.0, 150.0), 2),
        })
    return pd.DataFrame(products)


def _weighted_order_date() -> datetime:
    """
    Pick a date within the history window, with:
      - weekend lift (Sat/Sun ~1.6x weekdays)
      - linear growth over the year (recent weeks ~2x older ones)
    """
    now = datetime.now()
    start = now - timedelta(days=DAYS_OF_HISTORY)

    # Sample a candidate day biased toward recent dates by sampling days_ago
    # from a triangular distribution peaked at 0 (today).
    days_ago = int(random.triangular(0, DAYS_OF_HISTORY, 0))
    candidate = now - timedelta(days=days_ago)

    # Weekend lift: reject ~40% of weekday samples to bias toward weekends.
    if candidate.weekday() < 5 and random.random() < 0.25:
        # Nudge forward/backward to nearest weekend day
        offset = random.choice([-1, 1, 2])
        candidate = candidate + timedelta(days=offset)
        if candidate > now:
            candidate = now
        if candidate < start:
            candidate = start

    # Random time-of-day
    candidate = candidate.replace(
        hour=random.randint(8, 22),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=0,
    )
    # Final clamp: randomised time-of-day could push us past `now`
    if candidate > now:
        candidate = now
    return candidate


def _assign_order_counts(customer_ids: list[str]) -> dict[str, int]:
    """
    Assign a target number of orders per customer:
      - 70% of customers: exactly 1 order (one-and-done)
      - 25% of customers: 2-4 orders (repeat)
      - 5% of customers: 5-10 orders (loyal)
    """
    counts: dict[str, int] = {}
    for cid in customer_ids:
        r = random.random()
        if r < 0.70:
            counts[cid] = 1
        elif r < 0.95:
            counts[cid] = random.randint(2, 4)
        else:
            counts[cid] = random.randint(5, 10)
    return counts


def generate_orders(
    customers_df: pd.DataFrame, products_df: pd.DataFrame
) -> pd.DataFrame:
    orders = []

    # Build a pool of customer slots weighted by their target order count,
    # per tenant, so repeat behaviour is tenant-consistent.
    pools: dict[str, list[str]] = {}
    for tenant in TENANTS:
        tenant_customer_ids = customers_df.loc[
            customers_df["tenant_id"] == tenant, "customer_id"
        ].tolist()
        counts = _assign_order_counts(tenant_customer_ids)
        pool = []
        for cid, n in counts.items():
            pool.extend([cid] * n)
        random.shuffle(pool)
        pools[tenant] = pool

    utm_sources = list(UTM_WEIGHTS.keys())
    utm_probs = list(UTM_WEIGHTS.values())

    attempts = 0
    max_attempts = NUM_ORDERS * 4
    while len(orders) < NUM_ORDERS and attempts < max_attempts:
        attempts += 1
        tenant = pick_tenant()

        pool = pools[tenant]
        if not pool:
            continue
        tenant_products = products_df[products_df["tenant_id"] == tenant]
        if tenant_products.empty:
            continue

        customer_id = pool.pop()
        product = tenant_products.sample(1).iloc[0]

        quantity = random.randint(1, 4)
        gross_sales = round(product["price"] * quantity, 2)

        # ~20% of orders get a discount, varying 5-25%
        if random.random() < 0.20:
            discount_pct = random.uniform(0.05, 0.25)
            discount = round(gross_sales * discount_pct, 2)
        else:
            discount = 0.0
        net_sales = round(gross_sales - discount, 2)

        orders.append({
            "order_id": fake.unique.uuid4(),
            "tenant_id": tenant,
            "customer_id": customer_id,
            "product_id": product["product_id"],
            "order_date": _weighted_order_date().isoformat(),
            "quantity": quantity,
            "gross_sales": gross_sales,
            "discount": discount,
            "net_sales": net_sales,
            "utm_source": random.choices(utm_sources, weights=utm_probs, k=1)[0],
        })

    return pd.DataFrame(orders)


def generate_refunds(orders_df: pd.DataFrame) -> pd.DataFrame:
    refund_mask = [random.random() < REFUND_RATE for _ in range(len(orders_df))]
    refunded = orders_df[refund_mask].copy()

    now = datetime.now()
    refunds = []
    for _, order in refunded.iterrows():
        order_dt = datetime.fromisoformat(order["order_date"])
        refund_dt = order_dt + timedelta(days=random.randint(3, 14))
        # Clamp to now so we never generate future-dated refunds.
        # If an order is too recent for a realistic refund window, skip it.
        if refund_dt > now:
            continue
        # Partial refunds happen ~30% of the time, full otherwise
        if random.random() < 0.30:
            refund_amount = round(order["net_sales"] * random.uniform(0.2, 0.8), 2)
        else:
            refund_amount = order["net_sales"]

        refunds.append({
            "refund_id": fake.unique.uuid4(),
            "tenant_id": order["tenant_id"],
            "order_id": order["order_id"],
            "refund_date": refund_dt.isoformat(),
            "refund_amount": refund_amount,
            "reason": random.choice([
                "damaged", "wrong_size", "not_as_described",
                "changed_mind", "late_delivery",
            ]),
        })
    return pd.DataFrame(refunds)


# --- Main -----------------------------------------------------------------

def main() -> None:
    print("Generating Shopify mock data...")
    df_customers = generate_customers()
    df_products = generate_products()
    df_orders = generate_orders(df_customers, df_products)
    df_refunds = generate_refunds(df_orders)

    out_dir = Path(__file__).parent
    df_customers.to_csv(out_dir / "shopify_customers_bronze.csv", index=False)
    df_products.to_csv(out_dir / "shopify_products_bronze.csv", index=False)
    df_orders.to_csv(out_dir / "shopify_orders_bronze.csv", index=False)
    df_refunds.to_csv(out_dir / "shopify_refunds_bronze.csv", index=False)

    print(f"Customers: {len(df_customers)}")
    print(f"Products:  {len(df_products)}")
    print(f"Orders:    {len(df_orders)}")
    print(f"Refunds:   {len(df_refunds)}")
    print(f"Wrote CSVs to: {out_dir}")


if __name__ == "__main__":
    main()
