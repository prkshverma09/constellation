# Build decisions

Generated from the source responses on 2026-10-04.

- Honest-gap disease: `FRRS1L` / `MONDO:0014859`. Live PubMed hits: 11; ClinicalTrials.gov records: 0; verified foundation links: 0.
- Gap detector fired: No neighbour meets both S ≥ 0.45 and M ≥ 0.25 in the indexed cluster.; No reusable asset or investigator bridge is mapped to this cluster..
- Similarity layers: source-derived Monarch semantic-similarity results, lowest-level specific Reactome pathway sets, specific GO biological-process terms, filtered STRING mechanism scores, and literature-derived variant-class labels. No gene-name exceptions are used in the analytics.
- STRING mechanism uses `1 - (1-escore)*(1-dscore)`; text-mining is excluded because co-mention can inflate mechanism evidence. STRING supports M only when this score is at least 0.7; every channel score is retained in edge properties.
- Reactome pathways come from the human UniProt2Reactome all-level mapping. Specificity counts distinct human genes per pathway; only lowest-level pathways with counts ≤500 contribute to `M_reactome`.
- GO biological-process annotations use `goa_human.gaf.gz` and `go-basic.obo`, aspect P, excluding NOT qualifiers, propagated over `is_a`/`part_of`. Specificity counts distinct human genes assigned to each term or its descendants across all evidence codes; ≤500 is the GSEA default maximum gene-set size. Per-gene scoring uses experimental codes only, and `M_go` is the Jaccard of specific propagated terms.
- HPO information content uses `phenotype.hpoa` and `hp.json` `is_a` ancestry. Each term counts distinct HPOA diseases annotated to it or a descendant, independent of the slice. Asset profiles union served-disease phenotypes and HPO terms matched by exact outcome label/synonym; coverage accepts exact terms, descendants, and ancestors whose IC is at least half the target term IC, weighted by target IC.
- Mechanism support is the maximum of Reactome lowest-level Jaccard, specific GO Jaccard, and the thresholded STRING indicator. The support guard is `S >= 0.45 AND M >= 0.25`; weights and the 0.45 S threshold are unchanged. The previous `M > 0` guard admitted tiny incidental overlaps such as SLC2A1's single insulin-secretion pathway (M=0.083).
- Golden expectation revised: DNM1 removed from required members after evidence review (experimental GO / Reactome / physical STRING show no specific shared mechanism; DNM1 acts at vesicle endocytosis, the cluster core at SNARE exocytosis); it is surfaced as a partial overlap instead.
- Monarch pairwise scores use `/v3/api/semsim/multicompare` for the in-slice phenotype sets. `/v3/api/semsim/search` caps responses at 50 and omitted DNM1 from STXBP1's result set; multicompare provides a complete slice matrix without changing the weights.
- Animal-model assets were omitted unless a source-validated MGI allele record and live URL could be verified.
