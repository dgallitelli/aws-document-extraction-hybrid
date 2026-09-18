const invoiceFields = {
  invoice_number: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The supplier-assigned invoice, bill, or statement identifier.",
  },
  invoice_date: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The invoice or statement issue date in YYYY-MM-DD format.",
  },
  due_date: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The payment due date in YYYY-MM-DD format.",
  },
  supplier_name: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The legal or trading name of the supplier or issuer.",
  },
  customer_name: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The legal name of the billed customer or account holder.",
  },
  total_amount: {
    type: "number",
    inferenceType: "explicit",
    instruction:
      "The final total amount due as a decimal number without a currency symbol.",
  },
  currency: {
    type: "string",
    inferenceType: "explicit",
    instruction: "The three-letter ISO 4217 currency code.",
  },
};

export const documentInvoiceBlueprintSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  class: "Invoice",
  description:
    "A customer-agnostic invoice or bill containing supplier, customer, date, identifier, currency, and amount information.",
  properties: invoiceFields,
};

export const imageInvoiceBlueprintSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  class: "Invoice Image",
  description:
    "An image of an invoice or bill containing supplier, customer, date, identifier, currency, and amount information.",
  properties: invoiceFields,
};
