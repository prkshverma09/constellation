# Build decisions

Generated from the source responses on 2026-10-03.

- Honest-gap disease: `FRRS1L` / `MONDO:0014859`. Live PubMed hits: 11; ClinicalTrials.gov records: 0; verified foundation links: 0.
- Gap detector fired: No within-cluster mechanism neighbour meets the P/M/V support threshold.; No reusable asset or investigator bridge is mapped to this cluster..
- Similarity layers: source-derived Monarch semantic-similarity results, lowest-level Reactome pathway sets, STRING score ≥0.7, and literature-derived variant-class labels. No gene-name exceptions are used in the analytics.
- Monarch pairwise scores use `/v3/api/semsim/multicompare` for the in-slice phenotype sets. `/v3/api/semsim/search` caps responses at 50 and omitted DNM1 from STXBP1's result set; pairwise comparison provides a complete slice matrix without changing the weights.
- Animal-model assets were omitted unless a source-validated MGI allele record and live URL could be verified.
