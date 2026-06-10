const { pickTemplateFromDescription, buildDemoDataPlan, slugify, TEMPLATE_META } = require('./demoDataTemplates');

/**
 * Run demo data SQL plan on a SQL warehouse.
 * @param {typeof import('../server').executeSql} executeSql
 */
async function generateDemoDataInCatalog(executeSql, host, token, warehouseId, opts) {
  const {
    catalog,
    schema,
    description = '',
    templateKey: forcedTemplate,
    rowCount = 500,
  } = opts;

  if (!catalog || !schema) {
    throw new Error('catalog and schema are required');
  }
  if (!warehouseId) {
    throw new Error('warehouse_id is required to generate demo data');
  }

  const templateKey = forcedTemplate && TEMPLATE_META[forcedTemplate]
    ? forcedTemplate
    : pickTemplateFromDescription(description);

  const plan = buildDemoDataPlan({ catalog, schema, templateKey, rowCount });

  for (const sql of plan.statements) {
    await executeSql(host, token, warehouseId, sql);
  }

  return {
    templateKey: plan.templateKey,
    templateLabel: plan.label,
    catalog,
    schema,
    tables: plan.tables,
    ucMetadata: plan.tables.join(','),
  };
}

function buildDemoSchemaName(businessName, sessionId) {
  const slug = slugify(businessName);
  const sid = String(sessionId || Date.now()).replace(/\D/g, '').slice(-8);
  return `inspire_demo_${slug}_${sid}`.slice(0, 128);
}

module.exports = {
  generateDemoDataInCatalog,
  buildDemoSchemaName,
  pickTemplateFromDescription,
  TEMPLATE_META,
};
