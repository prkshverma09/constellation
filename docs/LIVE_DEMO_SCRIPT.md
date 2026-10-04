# Constellation — Live Demo Script (about 4 minutes)

**App URL:** https://3000--4c24548401544529ad2a62595d76f3e9.preview.devinapps.com/

Each step lists **DO** (what to click) and **SAY** (what to tell the audience).
The page for a disease is one long scrolling page. The links under the header (**Overview · Cluster · Assets · People**) jump to each section.

---

## Before you start (not on camera)

1. Open the App URL in a fresh tab. You should see a large search box with the placeholder *"Try STXBP1, Munc18-1, DEE4…"*.
2. Look at the top-right corner of the header. There should be a small badge reading **`cached · gpt-5`**, and next to it a persona switch: **Maria · Devon · Priya · Dr. Osei**. **Maria** should be selected; if not, click it.
3. Rules for the live demo:
   - **Do not click "Regenerate with GPT-5".** It runs the live AI agents for about 1.5–2.5 minutes, and the rest of the app freezes until it finishes. All dossiers are already generated.
   - If a page shows "Loading…", wait 2–3 seconds. Don't click repeatedly.

---

## Step 1 — The problem (20 s)

**DO:** Stay on the search page.

**SAY:**
> "There are over 10,000 rare diseases. Most have no study, no patient registry and no researcher working on them. Families end up alone with a diagnosis. Constellation connects your disease to others that share its biology, then shows what research already exists, who to contact, and what to do next. Every claim has a citation."

---

## Step 2 — Search a disease (15 s)

**DO:**
1. Click the search box and type **`STXBP1`**.
2. A line appears under the box: *"STXBP1 → developmental and epileptic encephalopathy, 4 (MONDO:0012812) …"*. Click that line, or press **Enter**.
3. The disease page opens: big title **"developmental and epileptic encephalopathy, 4"**, a row of count cards (Phenotypes, Pathogenic variants, Trials, Active awards, Papers, Patient groups), and a **Patient communities** box.

**SAY:**
> "I'm Maria. My child has STXBP1 disorder. Constellation recognises gene names, protein names and disease codes. It pulls real data from Monarch, ClinVar, ClinicalTrials.gov, NIH RePORTER, PubMed, Reactome, STRING and Gene Ontology. Patient groups are labelled 'curated' or 'web_discovered': GPT-5 found them with web search, and we checked each page really exists and names the gene."

---

## Step 3 — Who shares our biology (45 s)

**DO:**
1. Click **Cluster** in the section links (or scroll down). You'll see **"MECHANISM-FIRST CLUSTER — Neurotransmitter release cycle"** and a round map.
2. Under **Supported neighbours** are three cards: **STX1B, VAMP2, SNAP25**. Click the **STX1B** card to expand it. It shows P / M / V scores and **Layer evidence** links.
3. Click any small evidence link (e.g. under **M:**). The **Edge inspector** drawer opens on the right, showing the source, the record ID, the date retrieved and the evidence type. Close it with the **×** in its top-right corner.
4. Scroll a little to the **Counterexample** box: **GNAO1**.
5. Scroll to **Partial overlap**. Click **"Show all (6)"** and point at **DNM1** at the bottom of the list.

**SAY:**
> "We group diseases by shared *mechanism*, not just similar symptoms. STXBP1 sits with STX1B, VAMP2 and SNAP25: the proteins that release signals between nerve cells. Every link opens its source record."
>
> "This is GNAO1. Clinically it looks almost identical, but it shares no mechanism, so we exclude it and show you why. A symptom-only tool would have grouped it in."
>
> "DNM1 shares only one weak process, so it stays a *partial lead*, not a claim. We never upgrade weak evidence."

---

## Step 4 — What already exists (40 s)

**DO:**
1. Click **Assets** in the section links.
2. Find the dropdown labelled **"Compare assets against"**. Open it and choose **"DNM1 · developmental and epileptic encephalopathy, 31B"** (under the *Partial overlaps* group).
3. Find the card for **NCT06555965 — STXBP1 and SYNGAP1 Related Disorders Natural History Study** (recruiting, 600 participants, 5 sites). Point at the **coverage ≈ 12%** figure.
4. Point at the eligibility rows: **Genotype requirement → differs** and **Other-gene exclusion → differs**. Optionally open **"Full eligibility text"**.

**SAY:**
> "A recruiting natural-history study already exists for STXBP1 and SYNGAP1: 600 participants, 5 sites. It already measures about 12% of what matters for DNM1 families, weighted by how specific each symptom is. But its genotype rule shuts DNM1 families out. So the ask isn't 'build a new study', it's 'amend this one'. That saves years."

---

## Step 5 — Who to contact (25 s)

**DO:**
1. Click **People** in the section links. The dropdown **"Compare bridge people against"** should already show DNM1, because it follows the Assets choice. If it doesn't, pick DNM1 again.
2. Point at the **Ingo Helbig** card: Children's Hospital of Philadelphia, badges **Author · PI · Study official**, **"25 / 6 proving records · STXBP1 / DNM1"**.
3. Click **"Why same person"** on that card to expand it (matching ORCID, trial affiliation, shared co-author).
4. Point at the line above the cards: **"7 name-only matches excluded pending corroboration."**

**SAY:**
> "Ingo Helbig has published on both diseases, holds NIH funding and is an official on that very study. We confirm it's the same person with his ORCID ID and affiliation, not just a name match. Seven people who matched on name alone were left out. And no email addresses are shown: privacy by design."

---

## Step 6 — The Shared Path Dossier (50 s)

**DO:**
1. Scroll to the bottom of the page and click the blue **"Open Shared Path Dossier"** button.
2. The dossier page opens. Under the title, point at **"GPT-5 · generated <date>"**.
3. Scroll through the section headings: **Who shares our characteristics · What already exists · What differs and must be verified · Who to contact · Next step · Coverage**.
4. **Hover** over any small superscript number at the end of a sentence; a popup shows the evidence. **Click** it to open the Edge inspector, then close it with **×**.
5. In the header persona switch, click **Dr. Osei**. The dossier rewrites in a technical, verify-first style. Then click **Maria** again.
6. Optionally point at the **Export PDF**, **Copy Markdown** and **Download .md** buttons at the top.

**SAY:**
> "This is what Maria actually sends: a Shared Path Dossier. GPT-5 runs three agents. A Navigator plans the route, a Skeptic looks for contradictions, and a Writer drafts it for this reader. Every sentence carries citations. If a sentence can't be traced to a source record, or invents a number or a gene, our validator removes it."
>
> "Same evidence, different reader. Dr. Osei, a clinician-scientist, gets a technical version that starts with what to verify. Maria gets plain English she can email to a researcher. The next step is concrete: ask about the study's eligibility rule and contact the named investigator."

---

## Step 7 — Honest gap (35 s)

**DO:**
1. Click the **Constellation** logo (top left) to return to search.
2. Type **`FRRS1L`** and press **Enter**.
3. Click **Coverage** in the section links. You'll see the heading **"Honest gap report"**.
4. Point at **Sources checked**: PubMed **11**, ClinicalTrials.gov **0**, verified foundation seed list **0**, OpenAI web search (validated) **1**.
5. Point at **Nearest leads** (each says *"missing: mechanism layer"*) and **What would change this**.

**SAY:**
> "Not every disease has a path yet. FRRS1L has 11 papers, no trials, and no verified foundation. Most AI tools would invent a connection here. Constellation says plainly that there's no supported path yet. It shows every source it checked, the nearest leads and what is missing, and exactly which study would change the answer. This is a coverage gap, not a negative finding."

---

## Step 8 — Close (10 s)

**SAY:**
> "From an isolated diagnosis to a cited, sendable collaboration plan in minutes instead of years. Every claim is traceable, weak evidence stays weak, and when there's no path we say so."

---

## If something goes wrong

| Problem | Fix |
|---|---|
| Badge shows `offline` or a page won't load | Tell me; I'll check the servers. |
| Dossier label says "offline writer" instead of "GPT-5" | It's still a fully cited dossier from the deterministic writer. Carry on and say: "this one uses the offline writer, so it also works without an API key." |
| The app is frozen after someone clicked "Regenerate with GPT-5" | Wait up to 2–3 minutes; it finishes on its own. |
| The DNM1 option isn't in the dropdown | It's under the *Partial overlaps* group in the dropdown; scroll inside the list. |
