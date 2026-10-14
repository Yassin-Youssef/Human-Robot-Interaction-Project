# HRI robot trust study: analysis

Trust is the main outcome. Participants rate trust in the robot and in the screen (1 to 5) after each of the 3 levels, and at the end say which they trusted more overall. The analysis compares the two sources and links trust to following the advice and to correctness. The behavior hypotheses H2 to H4 are secondary.

## Quick start

```
pip install pandas openpyxl scipy matplotlib
python analyze.py HRI_data_entry.xlsx          (results go to the folder "results")
python analyze.py HRI_data_entry.xlsx --out my_results
```

| File | What it is |
|---|---|
| `analyze.py` | The analysis. Checks the typing, computes everything, writes the results. |
| `HRI_data_entry.xlsx` | Empty entry workbook with dropdowns and an `Example` sheet. |
| `FAKE_example_data.xlsx` | 40 made-up participants, to try the program. |
| `example_results/` | The results for the fake data (summary, Excel, charts). |
| `make_template.py` | Rebuilds the empty entry workbook. |
| `make_fake_data.py` | Fake data generator (`--null` for no true effect, `--n`, `--seed`). |
| `test_analysis.py` | Tests (`--quick` skips the long simulation). `test_output.txt` is its last full output. |

## The Excel file

Three sheets, one header row each, typed by hand. Column names must stay as they are.

- **Data**: participant, level, question, source, advice, correct, chosen, time_s. One row per question.
- **Trust**: participant, level, trust_robot, trust_screen, rated_first. One row per participant and level. Ratings are whole numbers 1 to 5.
- **Final**: participant, more_trusted (robot, screen or equal), reason. One row per participant.

Typos stop the program and name the sheet and the Excel row (for example `Trust sheet, row 7: rated_first must be robot or screen`). Missing optional values (an empty `chosen`, an empty rating, no Final row yet) only give a warning, and that value is left out. Design deviations (error positions, counts per source) also only warn. They are set in the `DESIGN` block at the top of `analyze.py`, and any rule can be switched off with `None`.

## What is computed

Derived in code: advice_wrong, followed (chosen = advice), accurate (chosen = correct), after_error (the previous question in the same level had a wrong advice), baseline, and per participant x level x source cells (follow rate, whether the wrong advice was followed, own accuracy, trust).

| ID | Question | Test | Role |
|---|---|---|---|
| P1 | Which source is trusted more? | Paired t-test on mean trust over 3 levels (Wilcoxon backup) | Primary |
| P2 | Is the robot followed more? (H1) | Paired t-test on follow rate | Primary |
| P3 | Is a source followed more when it is trusted more? | Within-person regression (participant intercepts, controls for source and level, cluster-robust SE) | Primary |
| S1 | Final overall choice | Exact binomial test, equal answers left out | Secondary |
| S2 | Trust vs being fooled by the source's wrong advice | Same regression as P3, Holm with S3 | Secondary |
| S3 | Trust vs own share correct on that source's questions | Same regression, Holm with S2 | Secondary |
| S3b | Trust vs the source's advice accuracy | Same regression. Only appears if advice accuracy varies between cells | Secondary |
| S4 | Trust difference vs follow-rate difference between people | Spearman correlation | Secondary |
| S5 | H2: following right after a wrong advice vs baseline | Within-person permutation test (labels shuffled within participant and level) | Secondary |
| S6 | H3: after robot vs after screen errors | Paired t-test | Exploratory |
| S7 | H4: follow rate hard vs easy | Paired t-test (Friedman backup) | Secondary |
| C1a, C1b | Difficulty checks: accuracy and decision time, hard vs easy | Paired t-test | Check |
| C2 | Does the rating order matter? | Welch t-test (paired if the order changes within people) | Check |

No correction for multiple tests on P1 to P3. Only S2 and S3 are adjusted together (Holm).

Outputs: `summary.txt` (trust results first, plain language), `HRI_results.xlsx` (all tables, including warnings, the list of final reasons and the cleaned data), and 8 charts (trust robot vs screen, trust by level, final choice, trust vs following, trust vs correctness, follow rate, follow after error, difficulty checks).

## How it was tested (`python test_analysis.py`)

- Key numbers recomputed by separate plain code (means, paired tests, the H2 next-question logic, the binomial test and the within-person regressions with explicit participant dummies).
- 19 typo files plus duplicates, a missing sheet and a misspelled column: each stops with the right sheet and row.
- About 20 edge cases (1 or 2 participants, everyone following everything, constant trust, no times, empty cells, missing Trust or Final rows, no wrong advice, errors on Q1, back to back or Q6, advice accuracy that differs between sources, rating order changing within a person, a changed design, messy typing): none crashes, and summary, Excel and charts are produced.
- 1000 simulated studies of 40 participants with no true effect anywhere: the share of significant tests is reported per test in `test_output.txt`, and should be near 5% (the exact binomial test S1 is conservative, so it sits a bit lower).

## Limitations

Levels always run easy, medium, hard, so difficulty is partly mixed up with fatigue and learning, and later trust ratings carry earlier experience. Trust is a single 1 to 5 item per source and level, so it is coarse, ordinal and may hit ceiling, and rating two sources back to back may invite comparison. Each source is wrong exactly once per level, so advice correctness cannot differ between sources by design. The trust-follow and trust-correctness links are associations within people: trust is rated after the level, so following came first and causation cannot be claimed. Whether participants were fooled is chosen by the participants and not by us. "Followed" mixes real reliance with agreement, because a participant who knows the answer also follows correct advice. Following wrong advice is cleaner, but there are only 3 per source per person. H3 rests on very few trials per person and is exploratory. S2 (being fooled) has only one wrong advice per source and level, so it has low power: in simulations with a moderate effect it was detected only about 10% of the time. The stopwatch is hand-operated, so times are only accurate to a few tenths of a second. With 40 participants, small effects can be missed (about 80% power for dz = 0.5 on the trust comparison).

## Assumptions

1. `rated_first` is fixed per participant. If it changes between levels, the order check switches to a within-person comparison automatically.
2. Trust is analyzed as an interval scale after averaging 3 ratings per source (paired t-test), with Wilcoxon reported as a backup.
3. Tests are two-sided at alpha = .05, even where a direction was predicted. The summary states whether the result matched the prediction.
4. H2 compares only trials whose own advice is correct (like with like), and the baseline starts at question 3 (`BASELINE_MIN_Q`), because the first question that can follow an error is question 3 when errors never sit on Q1. Its p-value comes from a permutation test, because following correct advice sits near 100% and with only 2 to 4 post-error trials per person the paired t-test came out significant in 8.5% of no-effect simulations. The permutation test assumes that, without an effect, a post-error trial is like any other trial of the same participant and level. Question position is not controlled beyond that.
5. H4 is tested as hard vs easy, which is the linear trend for three equally spaced levels. Friedman across all three is a backup.
6. The within-person regression needs participants whose predictor varies across their cells. Others do not contribute. With fewer than 15 contributing participants the standard errors may be too small, and the summary says so.
7. Equal answers in the final question are left out of the binomial test and are listed in the counts.
8. If a participant misses a rating or an answer, only that value is dropped. Participants with fewer than 3 usable values in a test are left out of it.
9. The decision-time test uses log time, because times are skewed. Means are reported as geometric means.
10. The final "reason" texts are listed but not coded or tested.
11. No trial-level logistic or GEE model is fitted. The paired tests answer the same questions with fewer assumptions.
12. Error positions are not decided yet, so the design checks only warn.
