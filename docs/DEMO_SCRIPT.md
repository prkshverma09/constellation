# Constellation — demo script (about 3 minutes)

Run `make demo` and open http://localhost:3000. The demo uses the committed snapshot in offline mode: no network and no API key needed.
Every number below came from the current snapshot. If the snapshot is rebuilt with `make data`, re-check the numbers before presenting.

## 0. Framing (15 s)
"Maria leads a patient group for STXBP1. Her question isn't 'what is STXBP1?' — rare-disease knowledge graphs already answer that. Her questions are: who shares our biology, what work already exists that we can reuse, who could we work with, and what should we do first? Constellation answers those with a cited dossier she can send."

## 1. Search → overview (20 s)
- Type `Munc18-1`. It resolves to *developmental and epileptic encephalopathy 4* (MONDO:0012812) through the synonym. `STXBP1`, `DEE4` and `STXBP1 Foundation` resolve to the same disease.
- Overview tiles: 55 HPO phenotypes, 608 pathogenic ClinVar variants, 11 ClinicalTrials.gov studies, 13 active NIH awards, 140 papers. Patient communities: STXBP1 Foundation is the exact match; Rare Epilepsy Network is an umbrella network.
- Click any tile's "View evidence" to show that every count is backed by edges.

## 2. Mechanism-first cluster (45 s)
- Cluster label: **Neurotransmitter release cycle** (Leiden clustering; stability 1.0 across 20 seeds).
- Supported neighbours: **STX1B, VAMP2, SNAP25**. Each row shows three layers:
  - P = phenotype similarity (Monarch);
  - M = mechanism (Reactome / experimental GO / physical STRING);
  - V = variant effect.
  - Support requires S ≥ 0.45 **and** M ≥ 0.25.
- **Counterexample: GNAO1.** It has the highest clinical similarity of any non-member (P 0.98) but M = 0, so it is excluded. "Looking alike is not the same as sharing a mechanism."
- **Partial overlap lane: DNM1.** It shares only "vesicle organization" (GO:0016050), so M = 0.03, which is below threshold. Our own starting hypothesis was that DNM1 belonged in this cluster. The evidence says DNM1 acts at a different step of the vesicle cycle (endocytosis rather than SNARE exocytosis), and its variant class differs (V = 0). Constellation shows that disagreement instead of hiding it.
- Open any row's footnote. The Edge Inspector shows source, record ID, retrieval date, evidence class, confidence, the verbatim quote and any contradicting edges.

## 3. Reusable assets (30 s)
- Switch the comparator to **DNM1** (the URL updates to `&vs=`).
- **NCT06555965**, "STXBP1 and SYNGAP1 Related Disorders Natural History Study": observational, recruiting, 600 participants, 5 sites. It already captures 12% of DNM1-related disease phenotype information (IC-weighted; 6 of 39 HPO terms). Eligibility diff: the genotype requirement and the other-gene exclusion **differ**, so DNM1 families would need a protocol amendment. "That is the concrete ask."

## 4. Bridge people (20 s)
- **Ingo Helbig** (Children's Hospital of Philadelphia): 25 STXBP1 and 6 DNM1 proving records, as author, NIH PI and the study official of NCT06555965. Identity is corroborated by ORCID plus the ClinicalTrials.gov affiliation and shared co-authors. Open "Why same person".
- Seven name-only matches were excluded pending corroboration. "We don't merge people on a name alone."

## 5. Shared Path Dossier (30 s)
- Open the dossier, persona Maria. Six sections: who shares our characteristics; what already exists; what differs and must be verified; who to contact; proposed first joint step; search coverage. Every sentence carries footnotes to ledger edges, and a validator drops any sentence it cannot cite.
- Switch to **Dr. Osei**: the same evidence, technical register, with verification items first. Then **Export PDF** or **Copy Markdown**.

## 6. Honest gap: FRRS1L (20 s)
- Search `FRRS1L` (MONDO:0014859). The page shows a gap report instead of a cluster. It lists the sources checked with counts (11 PubMed hits, 0 trials, 0 foundations), the nearest leads and the layer each is missing, and "what would change this", a specific evidence plan.
- "When the atlas doesn't know, it says so and tells you what evidence would change the answer."

## Close (10 s)
"Constellation turns a diagnosis into a justified, cited next step: a protocol amendment to an existing natural-history study, with the investigator who already bridges both communities. That takes weeks of desk work down to minutes, and every claim can be checked."

Disclaimer to keep on screen: research navigation, not medical advice.
