# Citing-impact, and what it is not

What the author page's "Citing-impact: software 96k · idea 863k" chip
actually measures, and how it sits beside the h-index and the rest of
the bibliometric furniture.

## TL;DR

* It is a **second-order** citation measure: not "how many papers
  cite you" but "how much weight do the papers that cite you carry".
  For each citing paper, it adds that paper's *own* citation count.
* It is split three ways by **what kind of work is being cited** —
  software, method, or idea. That split is the point of it, and the
  part nothing standard does.
* It is **not normalised** by field, career length or anything else,
  so it does not rank people. Two authors' numbers are not
  comparable. It answers a question about *one* author.
* The numbers are large — millions, for a long career — because they
  are sums over citers' citation counts. This is the honest source
  of the "why is that number so big?" reaction.
* A walk cut short by a rate limit used to cache its partial total
  as though it were complete. Fixed: the result now carries
  `complete`, and a partial one is never treated as fresh. See
  [Failure modes](#failure-modes).

## The definition

For an author:

1. List their works. Sort by citation count, keep the top *N*
   (`CITING_IMPACT_TOP_N_DEFAULT = 20`).
2. Classify each of those works as **software**, **method** or
   **idea** from its title (`metrics.classify_paper`).
3. For each sampled work, page through every paper that cites it,
   excluding the author's own papers (self-cites are filtered
   server-side by OpenAlex).
4. Add each citing paper's **`cited_by_count`** to the bucket of the
   work it cited. Within a bucket, a citing paper counts once however
   many of the author's works it cites; across buckets it can count
   in more than one, because the buckets answer three separate
   questions.

So `idea: 862,552` means: the papers that cite this author's
idea-classified work have, between them, been cited 862,552 times.

## A worked example

Two real cached rows, from this library's `author_scores` table:

| | software | | idea | |
|---|---|---|---|---|
| | works | citers · total | works | citers · total |
| Martin Steinegger | 5 | 3,664 · 95,699 | 15 | 56,672 · 862,552 |
| George M. Sheldrick | 5 | 0 · 0 | 13 | 49,800 · 1,434,345 |

Steinegger's row reads as a career whose software is used and whose
ideas are built upon, with the second an order of magnitude larger.
Sheldrick's software row reads as *nothing at all*, which for the
author of SHELX is plainly false — that was the defect below, not a
finding. Rows of that shape are now recomputed rather than believed.

## Why the split is the interesting part

A tool paper accrues citations that mean **"I used this"**. An idea
paper accrues citations that mean **"I built on this"**. Both are
called "citations" and summed into one number by every standard
metric, which is how a career in scientific software ends up looking
like a career in theory with better luck.

Coot's paper alone has some 65,000 citing papers. Collapsing that into
an h-index says the author is highly cited; it does not say that most
of that number is a measure of *adoption of a program*. Splitting by
the kind of the cited work says which it is, and lets the reader judge.

Nothing standard does this. Field normalisation (FWCI, SNIP) adjusts
for *where* a paper sits; it does not ask what kind of contribution it
was.

## Compared with the usual measures

| Measure | What it counts | Order | Normalised? | Blind to |
|---|---|---|---|---|
| **Total citations** | citations received | 1st | no | everything about who cited |
| **h-index** | largest *h* with *h* papers of ≥ *h* citations | 1st | no | the tail *and* the peak — a 10,000-citation paper counts as one |
| **i10** | papers with ≥ 10 citations | 1st | no | magnitude entirely |
| **g-index** | top *g* papers averaging ≥ *g* citations | 1st | no | designed to *restore* sensitivity to the peak h discards |
| **m-index** | h ÷ years since first paper | 1st | by career length | field, and everything h is blind to |
| **FWCI** | citations ÷ world average for field, year and type | 1st | yes | what kind of work it was |
| **Eigenfactor / SJR** | citations weighted by the citing *venue's* standing | 2nd | yes | the author; these are journal-level |
| **Citing-impact** | **citations of the citing papers, by kind of cited work** | **2nd** | **no** | field, career length, comparability |
| **Altmetrics** | attention: news, policy, social | — | no | scholarly use |

The closest relative is **Eigenfactor**, which also asks "who is doing
the citing, and do they matter?" — but does it with damping,
normalisation and full graph iteration, at journal level.
Citing-impact is one hop, unnormalised, at author level, split by kind.
It is a descriptive statistic, not a ranking, and should be read as
one.

The h-index remains the better *summary*: robust, well understood, and
unmoved by one blockbuster. Citing-impact is the opposite — dominated
by the blockbuster, which is exactly what makes it informative about
software, where one paper usually *is* the career's visible surface.

## Cost, caching, sampling

* The walk is the slowest thing in the application: minutes for a
  prolific author, ~700 API calls if `top_n=None`.
* Top-20 sampling cuts that 5–10× at little cost to the total, because
  citation distributions are so skewed. For Cowtan, the top 20 works
  cover over 95% of all citers.
* Results are cached 30 days in `author_scores`; the chip marks a
  stale result rather than hiding it.
* Because of sampling, every total is a **lower bound**, and the
  `n_works` shown is the sampled count, not the author's output.

## Failure modes

1. **A truncated walk used to be cached as a result.** In the citer
   loop, a failed request (`data is None` — rate limit, tripped
   breaker, timeout) `break`s out. What had accumulated was returned
   and cached for 30 days with no marker, which is what produced
   Sheldrick's "5 software works, 0 citers": a zero indistinguishable
   from "we could not ask".

   **Fixed.** `compute_citing_impact` returns `complete`,
   `works_walked` and `works_truncated`; the cache stores the flag;
   `_author_score_is_fresh` refuses a partial row however recent, so
   it is recomputed rather than shown for a month; and the chip reads
   "(partial)" instead of "(stale)", a floor being the more important
   caveat. Rows written before the column exists cannot say, so their
   *shape* is read instead: works in a bucket with no citing papers at
   all. On this library that distrusts two rows and leaves nine.
2. **Classification is title-word matching.** `classify_paper` looks
   for "software", "package", "algorithm", "method" and friends. It
   misses software with a bare name ("MultiCharge: quantum charge
   analysis" → idea) and catches reviews *of* methods. The per-paper
   `paper_kind` override is the escape hatch.
3. **Buckets are not disjoint.** A paper citing both a tool and an
   idea by the same author counts in both, so the three numbers do
   not sum to anything meaningful. Deliberate, but it means "total
   citing-impact" is not a quantity.
4. **The magnitude reads as a mistake.** "idea 2.3M" beside
   "85,493 citations" invites the reader to think something is
   broken. The tooltip explains it; the chip alone does not.

## If it were revisited

* Normalise optionally by the author's total citations, giving a
  dimensionless "the people who cite me are cited N× as much as I am"
  — comparable across careers in a way the raw sum is not.
* Weight by recency: a 1990s method paper's citers have had thirty
  years to accumulate their own citations.
* Take the kind from OpenAlex `type` and the presence of a software
  registry identifier rather than title words.
