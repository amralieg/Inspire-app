/**
 * Demo data templates for "I don't have data" flow.
 * Creates Delta tables via SQL (warehouse) — no Spark cluster required.
 */

const TEMPLATE_META = {
  saas: {
    label: 'SaaS / Subscriptions',
    keywords: ['saas', 'subscription', 'mrr', 'churn', 'billing', 'cloud', 'software'],
    tables: ['customers', 'subscriptions', 'usage_events', 'invoices'],
  },
  retail: {
    label: 'Retail / E-commerce',
    keywords: ['retail', 'ecommerce', 'e-commerce', 'store', 'product', 'order', 'shop'],
    tables: ['customers', 'products', 'orders', 'order_items'],
  },
  healthcare: {
    label: 'Healthcare',
    keywords: ['health', 'hospital', 'patient', 'clinical', 'medical', 'claim'],
    tables: ['patients', 'encounters', 'diagnoses', 'claims'],
  },
};

function qIdent(...parts) {
  return parts.map((p) => `\`${String(p).replace(/`/g, '``')}\``).join('.');
}

/**
 * Pick template from free-text description.
 * @returns {'saas'|'retail'|'healthcare'}
 */
function pickTemplateFromDescription(description) {
  const text = String(description || '').toLowerCase();
  let best = 'saas';
  let bestScore = 0;
  for (const [key, meta] of Object.entries(TEMPLATE_META)) {
    let score = 0;
    for (const kw of meta.keywords) {
      if (text.includes(kw)) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      best = key;
    }
  }
  return best;
}

/**
 * SQL statements to create schema + Delta tables + synthetic rows.
 * @param {{ catalog: string, schema: string, templateKey: string, rowCount: number }} opts
 * @returns {{ templateKey: string, label: string, tables: string[], statements: string[] }}
 */
function buildDemoDataPlan({ catalog, schema, templateKey, rowCount = 500 }) {
  const key = TEMPLATE_META[templateKey] ? templateKey : 'saas';
  const meta = TEMPLATE_META[key];
  const n = Math.min(Math.max(Number(rowCount) || 500, 100), 5000);
  const cs = qIdent(catalog, schema);
  const statements = [`CREATE SCHEMA IF NOT EXISTS ${cs}`];
  const fullTables = [];

  if (key === 'saas') {
    const customers = `${cs}.customers`;
    const subscriptions = `${cs}.subscriptions`;
    const usage = `${cs}.usage_events`;
    const invoices = `${cs}.invoices`;
    fullTables.push(customers, subscriptions, usage, invoices);

    statements.push(
      `CREATE TABLE IF NOT EXISTS ${customers} (
        customer_id BIGINT,
        customer_name STRING,
        segment STRING,
        country STRING,
        signup_date DATE
      ) USING DELTA`,
      `INSERT OVERWRITE ${customers}
       SELECT id AS customer_id,
              concat('Customer ', cast(id AS STRING)) AS customer_name,
              CASE WHEN id % 3 = 0 THEN 'Enterprise' WHEN id % 3 = 1 THEN 'Mid-Market' ELSE 'SMB' END AS segment,
              CASE WHEN id % 5 = 0 THEN 'US' WHEN id % 5 = 1 THEN 'UK' WHEN id % 5 = 2 THEN 'DE' ELSE 'FR' END AS country,
              date_add(DATE '2020-01-01', cast(id % 1800 AS INT)) AS signup_date
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${subscriptions} (
        subscription_id BIGINT,
        customer_id BIGINT,
        plan_name STRING,
        mrr DOUBLE,
        status STRING,
        start_date DATE
      ) USING DELTA`,
      `INSERT OVERWRITE ${subscriptions}
       SELECT id AS subscription_id,
              (id % ${n}) + 1 AS customer_id,
              CASE WHEN id % 4 = 0 THEN 'Enterprise' WHEN id % 4 = 1 THEN 'Pro' WHEN id % 4 = 2 THEN 'Growth' ELSE 'Starter' END AS plan_name,
              round(50 + (id % 500) * 1.25, 2) AS mrr,
              CASE WHEN id % 17 = 0 THEN 'churned' WHEN id % 11 = 0 THEN 'paused' ELSE 'active' END AS status,
              date_add(DATE '2021-06-01', cast(id % 900 AS INT)) AS start_date
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${usage} (
        event_id BIGINT,
        customer_id BIGINT,
        feature_name STRING,
        event_ts TIMESTAMP
      ) USING DELTA`,
      `INSERT OVERWRITE ${usage}
       SELECT id AS event_id,
              (id % ${n}) + 1 AS customer_id,
              CASE WHEN id % 6 = 0 THEN 'api_calls' WHEN id % 6 = 1 THEN 'dashboard' WHEN id % 6 = 2 THEN 'export' ELSE 'login' END AS feature_name,
              timestampadd(HOUR, -cast(id % 720 AS INT), current_timestamp()) AS event_ts
       FROM range(${Math.min(n * 3, 5000)})`,
      `CREATE TABLE IF NOT EXISTS ${invoices} (
        invoice_id BIGINT,
        customer_id BIGINT,
        amount DOUBLE,
        paid_flag BOOLEAN,
        invoice_date DATE
      ) USING DELTA`,
      `INSERT OVERWRITE ${invoices}
       SELECT id AS invoice_id,
              (id % ${n}) + 1 AS customer_id,
              round(100 + (id % 300) * 2.5, 2) AS amount,
              id % 9 <> 0 AS paid_flag,
              date_add(DATE '2022-01-01', cast(id % 1000 AS INT)) AS invoice_date
       FROM range(${n})`,
    );
  } else if (key === 'retail') {
    const customers = `${cs}.customers`;
    const products = `${cs}.products`;
    const orders = `${cs}.orders`;
    const items = `${cs}.order_items`;
    fullTables.push(customers, products, orders, items);

    statements.push(
      `CREATE TABLE IF NOT EXISTS ${customers} (
        customer_id BIGINT, customer_name STRING, region STRING, loyalty_tier STRING
      ) USING DELTA`,
      `INSERT OVERWRITE ${customers}
       SELECT id, concat('Shopper ', cast(id AS STRING)),
              CASE WHEN id % 4 = 0 THEN 'EMEA' WHEN id % 4 = 1 THEN 'APAC' ELSE 'Americas' END,
              CASE WHEN id % 5 = 0 THEN 'Gold' WHEN id % 5 = 1 THEN 'Silver' ELSE 'Bronze' END
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${products} (
        product_id BIGINT, product_name STRING, category STRING, unit_price DOUBLE
      ) USING DELTA`,
      `INSERT OVERWRITE ${products}
       SELECT id, concat('Product ', cast(id AS STRING)),
              CASE WHEN id % 5 = 0 THEN 'Electronics' WHEN id % 5 = 1 THEN 'Apparel' ELSE 'Home' END,
              round(5 + (id % 200) * 0.75, 2)
       FROM range(${Math.min(n, 200)})`,
      `CREATE TABLE IF NOT EXISTS ${orders} (
        order_id BIGINT, customer_id BIGINT, order_date DATE, order_total DOUBLE
      ) USING DELTA`,
      `INSERT OVERWRITE ${orders}
       SELECT id, (id % ${n}) + 1, date_add(DATE '2023-01-01', cast(id % 600 AS INT)), round(20 + (id % 150), 2)
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${items} (
        line_id BIGINT, order_id BIGINT, product_id BIGINT, quantity INT, line_amount DOUBLE
      ) USING DELTA`,
      `INSERT OVERWRITE ${items}
       SELECT id, (id % ${n}) + 1, (id % 200) + 1, (id % 5) + 1, round((id % 50) * 3.2, 2)
       FROM range(${Math.min(n * 2, 5000)})`,
    );
  } else {
    const patients = `${cs}.patients`;
    const encounters = `${cs}.encounters`;
    const diagnoses = `${cs}.diagnoses`;
    const claims = `${cs}.claims`;
    fullTables.push(patients, encounters, diagnoses, claims);

    statements.push(
      `CREATE TABLE IF NOT EXISTS ${patients} (
        patient_id BIGINT, patient_name STRING, age INT, primary_insurer STRING
      ) USING DELTA`,
      `INSERT OVERWRITE ${patients}
       SELECT id, concat('Patient ', cast(id AS STRING)), 18 + (id % 70),
              CASE WHEN id % 3 = 0 THEN 'Medicare' WHEN id % 3 = 1 THEN 'Commercial' ELSE 'Self-pay' END
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${encounters} (
        encounter_id BIGINT, patient_id BIGINT, encounter_type STRING, encounter_date DATE
      ) USING DELTA`,
      `INSERT OVERWRITE ${encounters}
       SELECT id, (id % ${n}) + 1,
              CASE WHEN id % 4 = 0 THEN 'Inpatient' WHEN id % 4 = 1 THEN 'Outpatient' ELSE 'ER' END,
              date_add(DATE '2022-03-01', cast(id % 800 AS INT))
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${diagnoses} (
        diagnosis_id BIGINT, encounter_id BIGINT, icd_code STRING, severity STRING
      ) USING DELTA`,
      `INSERT OVERWRITE ${diagnoses}
       SELECT id, (id % ${n}) + 1, concat('ICD', lpad(cast(1000 + (id % 900) AS STRING), 4, '0')),
              CASE WHEN id % 3 = 0 THEN 'High' WHEN id % 3 = 1 THEN 'Medium' ELSE 'Low' END
       FROM range(${n})`,
      `CREATE TABLE IF NOT EXISTS ${claims} (
        claim_id BIGINT, encounter_id BIGINT, billed_amount DOUBLE, paid_amount DOUBLE, claim_status STRING
      ) USING DELTA`,
      `INSERT OVERWRITE ${claims}
       SELECT id, (id % ${n}) + 1, round(500 + (id % 400) * 4.2, 2), round(300 + (id % 400) * 2.8, 2),
              CASE WHEN id % 7 = 0 THEN 'Denied' WHEN id % 5 = 0 THEN 'Pending' ELSE 'Paid' END
       FROM range(${n})`,
    );
  }

  return {
    templateKey: key,
    label: meta.label,
    tables: fullTables,
    statements,
  };
}

function slugify(name) {
  return String(name || 'demo')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '')
    .slice(0, 40) || 'demo';
}

module.exports = {
  TEMPLATE_META,
  pickTemplateFromDescription,
  buildDemoDataPlan,
  slugify,
};
