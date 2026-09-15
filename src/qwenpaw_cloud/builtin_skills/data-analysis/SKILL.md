---
name: data-analysis
description: Analyze uploaded tabular data and produce reproducible findings.
version: "1.0.0"
---

# Data analysis

Use pandas for uploaded CSV, TSV, JSON, or spreadsheet data. First report data
shape, inferred types, missing values, duplicates, and parsing limitations.
Keep transformations reproducible in `workspace/`, state filters and business
definitions, and publish tables/charts only from `output/`. Never invent rows or
silently coerce failed values.
