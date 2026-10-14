"""
Tests for analyze.py. Run:   python test_analysis.py            (everything, about 5 minutes)
                              python test_analysis.py --quick    (no 1000-run simulation, about 1 minute)

1. Recount: key numbers are recomputed by separate plain code and compared.
2. Typos: broken Excel files must stop the analysis and name the sheet and Excel row.
3. Edge cases: odd but valid data must not crash (summary, Excel and charts are all produced).
4. Simulation with no true effect: about 5% of the tests should come out significant.
"""
import io
import os
import subprocess
import sys
import tempfile
import contextlib
import time

import numpy as np
import pandas as pd
import openpyxl
from scipy import stats

import analyze as A
from make_fake_data import generate
from make_template import build_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS, PASSES = [], 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("  FAIL:", msg)


def quiet_run(d, t, f):
    """Run the whole pipeline (without charts). Returns (R, warns)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return A.run(d.copy(), t.copy(), f.copy())


def full_pipeline(d, t, f, tmp):
    """Everything main() does: check, compute, summary text, Excel and charts."""
    R, warns = quiet_run(d, t, f)
    A.write_excel(R["T"], warns, os.path.join(tmp, "r.xlsx"))
    files = A.charts(R, tmp)
    text = A.build_summary(R, warns)
    return R, warns, text, files


# --------------------------------------------------------------------------
def test_recount():
    print("1. Recount against separate code")
    d, t, f = generate(30, seed=5)
    R, warns = quiet_run(d, t, f)
    tests = {x["id"]: x for x in R["tests"]}

    # trust: plain loops
    rob = t.groupby("participant")["trust_robot"].mean()
    scr = t.groupby("participant")["trust_screen"].mean()
    tt = stats.ttest_rel(rob, scr)
    check(abs(tests["P1"]["p"] - tt.pvalue) < 1e-12, "P1 p-value equals scipy ttest_rel on hand-made means")
    check(abs(tests["P1"]["mean_1"] - rob.mean()) < 1e-12 and abs(tests["P1"]["mean_2"] - scr.mean()) < 1e-12,
          "P1 means recounted")

    # follow rates: plain python loop
    fr = {}
    for p, g in d.groupby("participant"):
        for s in ("robot", "screen"):
            rows = g[g["source"] == s]
            fr[(p, s)] = sum(1 for _, r in rows.iterrows() if r["chosen"] == r["advice"]) / len(rows)
    a_ = [fr[(p, "robot")] for p in sorted(d["participant"].unique())]
    b_ = [fr[(p, "screen")] for p in sorted(d["participant"].unique())]
    check(abs(tests["P2"]["p"] - stats.ttest_rel(a_, b_).pvalue) < 1e-12, "P2 recounted with plain loops")

    # H2: next-question logic recounted by hand
    post, base = {}, {}
    for (p, lvl), g in d.groupby(["participant", "level"]):
        g = g.set_index("question")
        for q in g.index:
            wrong_here = g.at[q, "advice"] != g.at[q, "correct"]
            if wrong_here:
                continue
            prev_wrong = (q - 1) in g.index and g.at[q - 1, "advice"] != g.at[q - 1, "correct"]
            fol = float(g.at[q, "chosen"] == g.at[q, "advice"])
            if prev_wrong:
                post.setdefault(p, []).append(fol)
            elif q >= A.BASELINE_MIN_Q:
                base.setdefault(p, []).append(fol)
    ps = sorted(set(post) & set(base))
    ref = stats.ttest_rel([np.mean(post[p]) for p in ps], [np.mean(base[p]) for p in ps])
    check(abs(tests["S5"]["p_t_test"] - ref.pvalue) < 1e-12 and tests["S5"]["n"] == len(ps), "S5 (H2) recounted with plain loops")
    check(0 < tests["S5"]["p"] <= 1, "S5 permutation p-value is a valid probability")

    # S1 binomial
    k = int((f["more_trusted"] == "robot").sum()); n = k + int((f["more_trusted"] == "screen").sum())
    check(abs(tests["S1"]["p"] - stats.binomtest(k, n, 0.5).pvalue) < 1e-12, "S1 binomial recounted")

    # P3 / S2 / S3: independent implementation with explicit participant dummies and explicit cluster loop
    cells = R["cells"].dropna(subset=["trust"]).copy()
    for xcol, tid in [("follow_rate", "P3"), ("fooled", "S2"), ("own_accuracy", "S3")]:
        c = cells.dropna(subset=[xcol])
        c = c[c.groupby("participant")[xcol].transform("nunique") >= 2]
        ids = sorted(c["participant"].unique())
        X = np.column_stack([c[xcol], (c["source"] == "robot").astype(float), (c["level"].astype(str) == "medium").astype(float),
                             (c["level"].astype(str) == "hard").astype(float)] +
                            [(c["participant"] == p).astype(float) for p in ids])
        y = c["trust"].to_numpy(float)
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        e = y - X @ beta
        bread = np.linalg.inv(X.T @ X)
        meat = np.zeros((X.shape[1], X.shape[1]))
        for p in ids:
            m = (c["participant"] == p).to_numpy()
            sc = X[m].T @ e[m]
            meat += np.outer(sc, sc)
        G = len(ids)
        se = np.sqrt((bread @ meat @ bread)[0, 0] * G / (G - 1))
        res = tests[tid]
        check(abs(res["estimate"] - beta[0]) < 1e-8, f"{tid} slope equals dummy-variable regression")
        check(abs(res["stat"] - beta[0] / se) < 1e-6, f"{tid} cluster-robust t equals explicit recomputation")
    print(f"   {PASSES} checks passed so far")


# --------------------------------------------------------------------------
def typo_cases():
    # (sheet, excel row, column, bad value, text expected in the message)
    return [
        ("Data", 5, "level", "esay", "Data sheet, row 5: level must be"),
        ("Data", 6, "source", "robt", "Data sheet, row 6: source must be"),
        ("Data", 7, "advice", "bleu", "Data sheet, row 7: advice must be"),
        ("Data", 8, "correct", "green", "Data sheet, row 8: correct must be"),
        ("Data", 9, "chosen", "rd", "Data sheet, row 9: chosen must be"),
        ("Data", 10, "question", 7, "Data sheet, row 10: question must be"),
        ("Data", 11, "question", 0, "Data sheet, row 11: question must be"),
        ("Data", 12, "time_s", "4.2s", "Data sheet, row 12: time_s must be"),
        ("Data", 13, "time_s", -1, "Data sheet, row 13: time_s must be above 0"),
        ("Data", 14, "participant", None, "Data sheet, row 14: participant is empty"),
        ("Trust", 3, "trust_robot", 6, "Trust sheet, row 3: trust_robot must be"),
        ("Trust", 4, "trust_screen", 0, "Trust sheet, row 4: trust_screen must be"),
        ("Trust", 5, "trust_robot", 3.5, "Trust sheet, row 5: trust_robot must be"),
        ("Trust", 6, "trust_screen", "four", "Trust sheet, row 6: trust_screen must be"),
        ("Trust", 7, "rated_first", "both", "Trust sheet, row 7: rated_first must be"),
        ("Trust", 8, "level", "hrad", "Trust sheet, row 8: level must be"),
        ("Trust", 9, "participant", "P99", "Trust sheet, row 9: this participant does not appear"),
        ("Final", 2, "more_trusted", "robo", "Final sheet, row 2: more_trusted must be"),
        ("Final", 3, "participant", "P1", "Final sheet, row 3: this participant does not appear"),
    ]


def test_typos(tmp):
    print("2. Typo files must stop with the right sheet and row")
    d, t, f = generate(4, seed=2)
    for sheet, row, col, val, expect in typo_cases():
        path = os.path.join(tmp, "typo.xlsx")
        build_workbook(path, d, t, f)
        wb = openpyxl.load_workbook(path)
        ws = wb[sheet]
        colidx = [c.value for c in ws[1]].index(col) + 1
        ws.cell(row=row, column=colidx).value = val
        wb.save(path)
        r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), path, "--out", os.path.join(tmp, "o")],
                           capture_output=True, text=True, encoding="utf-8")
        check(r.returncode != 0 and expect in r.stdout, f"{sheet} row {row} {col}={val!r}: expected '{expect}', got: {r.stdout.strip()[:160]!r}")
    # duplicate rows
    path = os.path.join(tmp, "dup.xlsx")
    build_workbook(path, pd.concat([d, d.iloc[[3]]]), t, f)
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), path, "--out", os.path.join(tmp, "o")],
                       capture_output=True, text=True, encoding="utf-8")
    last = len(d) + 2
    check(f"Data sheet, row {last}: the same participant, level and question appears more than once" in r.stdout
          and "Data sheet, row 5:" in r.stdout, "duplicate Data row reported for both copies")
    path = os.path.join(tmp, "dup2.xlsx")
    build_workbook(path, d, pd.concat([t, t.iloc[[0]]]), pd.concat([f, f.iloc[[0]]]))
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), path, "--out", os.path.join(tmp, "o")],
                       capture_output=True, text=True, encoding="utf-8")
    check("Trust sheet, row 2: the same participant and level appears more than once" in r.stdout
          and "Final sheet, row 2: the same participant appears more than once" in r.stdout, "duplicate Trust and Final rows reported")
    # missing sheet and column
    wb = openpyxl.load_workbook(os.path.join(tmp, "dup2.xlsx")); del wb["Final"]; wb.save(os.path.join(tmp, "nofinal.xlsx"))
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), os.path.join(tmp, "nofinal.xlsx")],
                       capture_output=True, text=True, encoding="utf-8", cwd=tmp)
    check(r.returncode != 0 and 'no sheet called "Final"' in (r.stdout + r.stderr), "missing Final sheet gives a clear error")
    wb = openpyxl.load_workbook(os.path.join(tmp, "dup2.xlsx")); wb["Trust"].cell(row=1, column=3, value="trust_robo"); wb.save(os.path.join(tmp, "nocol.xlsx"))
    r = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), os.path.join(tmp, "nocol.xlsx")],
                       capture_output=True, text=True, encoding="utf-8", cwd=tmp)
    check(r.returncode != 0 and "missing these columns" in (r.stdout + r.stderr), "misspelled column gives a clear error")
    print(f"   {PASSES} checks passed so far")


# --------------------------------------------------------------------------
def edge_cases(tmp):
    print("3. Edge cases must not crash")
    base = generate(12, seed=3)

    def case(name, d, t, f, expect_warn=None, expect_text=None):
        try:
            R, warns, text, files = full_pipeline(d, t, f, tmp)
        except BaseException as e:   # SystemExit counts as a failure here
            check(False, f"{name}: crashed with {type(e).__name__}: {e}")
            return None
        check(len(files) >= 1 and "PART 1: TRUST" in text, f"{name}: summary and charts produced")
        if expect_warn:
            check(any(expect_warn in w for w in warns), f"{name}: warning containing '{expect_warn}' (got {warns[:3]})")
        if expect_text:
            check(expect_text in text, f"{name}: summary contains '{expect_text}'")
        return R, text

    d, t, f = [x.copy() for x in base]
    case("normal 12 participants", d, t, f)

    d1, t1, f1 = generate(1, seed=3)
    case("one participant", d1, t1, f1, expect_text="too few")
    d2, t2, f2 = generate(2, seed=3)
    case("two participants", d2, t2, f2)

    # everyone follows everything, trust identical
    d, t, f = [x.copy() for x in base]
    d["chosen"] = d["advice"]
    t["trust_robot"], t["trust_screen"] = 4, 4
    case("everyone follows everything and trust constant", d, t, f)

    # nobody ever follows (chosen is the correct bowl), trust differs by exactly 1 for everybody
    d, t, f = [x.copy() for x in base]
    d["chosen"] = d["correct"]
    t["trust_robot"], t["trust_screen"] = 4, 3
    case("always correct, constant trust difference", d, t, f, expect_text="exactly the same difference")

    # no time column values
    d, t, f = [x.copy() for x in base]
    d["time_s"] = np.nan
    case("no decision times", d, t, f, expect_text="no times entered")

    # empty chosen
    d, t, f = [x.copy() for x in base]
    d.loc[[2, 30, 31], "chosen"] = np.nan
    case("empty chosen", d, t, f, expect_warn="chosen is empty")

    # missing trust for a level, missing rating cell, missing rated_first
    d, t, f = [x.copy() for x in base]
    t = t.drop(index=[1, 2]).reset_index(drop=True)
    t.loc[0, "trust_robot"] = np.nan
    t.loc[3, "rated_first"] = np.nan
    case("missing trust rows and cells", d, t, f, expect_warn="trust_robot is empty")

    # nobody in Final / all Final rows missing
    d, t, f = [x.copy() for x in base]
    case("no Final rows at all", d, t, f.iloc[0:0], expect_warn="no row in the Final sheet")

    # everyone says robot / everyone equal
    d, t, f = [x.copy() for x in base]
    f["more_trusted"] = "robot"
    case("everyone final=robot", d, t, f)
    f["more_trusted"] = "equal"
    case("everyone final=equal", d, t, f, expect_text="nobody chose")

    # design deviations: error on Q1, back to back, error on Q6
    d, t, f = [x.copy() for x in base]
    m = (d["participant"] == "P01") & (d["level"] == "easy")
    d.loc[m, "advice"] = np.where(d.loc[m, "question"].isin([1, 2, 6]), np.where(d.loc[m, "correct"] == "red", "blue", "red"),
                                  d.loc[m, "correct"])
    R, text = case("errors on Q1, Q2 and Q6", d, t, f, expect_warn="wrong advice on question 1")
    _, w = quiet_run(d, t, f)
    check(any("back to back" in x for x in w) and any("question 6" in x for x in w), "back-to-back and last-question warnings")

    # no wrong advice at all
    d, t, f = [x.copy() for x in base]
    d["advice"] = d["correct"]
    case("no wrong advices at all", d, t, f)

    # advice accuracy differs between sources (3 wrong robot advices): S3b must appear
    d, t, f = [x.copy() for x in base]
    r_rows = d[(d["source"] == "robot") & (d["level"] == "hard")].index
    d.loc[r_rows, "advice"] = np.where(d.loc[r_rows, "correct"] == "red", "blue", "red")
    out = case("robot always wrong in hard", d, t, f)
    check(out is not None and any(x["id"] == "S3b" for x in out[0]["tests"]), "S3b test appears when advice accuracy varies")

    # rated_first alternating within participants
    d, t, f = [x.copy() for x in base]
    t["rated_first"] = ["robot", "screen", "robot"] * (len(t) // 3)
    out = case("rated_first alternates within people", d, t, f)
    check(out is not None and "within participants" in str(find_test(out[0], "C2").get("design")), "C2 switches to a within-person comparison")

    # one question per level less (5 questions)
    d, t, f = [x.copy() for x in base]
    d = d[~((d["question"] == 6) & (d["participant"] == "P03"))]
    case("a participant with 17 rows", d, t, f, expect_warn="has 5 questions instead of 6")

    # another design: 4 questions only if config changed
    old = (A.QUESTIONS_PER_LEVEL, dict(A.DESIGN))
    A.QUESTIONS_PER_LEVEL = 8
    A.DESIGN.update({"robot_per_level": None, "wrong_per_level": None, "wrong_per_source_per_level": None})
    d, t, f = [x.copy() for x in base]
    d.loc[d.index[0], "question"] = 8
    case("configurable design (8 questions allowed)", d, t, f)
    A.QUESTIONS_PER_LEVEL = old[0]; A.DESIGN.clear(); A.DESIGN.update(old[1])

    # Excel file with messy typing: caps, spaces, comma decimals, blank rows, unsorted rows, IDs lower case
    d, t, f = [x.copy() for x in base]
    path = os.path.join(tmp, "messy.xlsx")
    build_workbook(path, d.sample(frac=1, random_state=1), t, f)
    wb = openpyxl.load_workbook(path)
    ws = wb["Data"]
    ws["A2"].value = " p05 "; ws["D2"].value = "ROBOT"; ws["H2"].value = "4,2"
    ws.delete_rows(10, 1)                       # blank gap row in the middle
    ws.insert_rows(10)
    wb.save(path)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        dd, tt, ff = A.read_workbook(path)
        errors, warns, *_ = A.clean_inputs(dd, tt, ff)
    check(not errors, f"messy but valid Excel passes the check (errors: {errors[:2]})")
    print(f"   {PASSES} checks passed so far")


def find_test(R, tid):
    return next(x for x in R["tests"] if x["id"] == tid)


# --------------------------------------------------------------------------
def simulate(n_sims, null, n=40):
    sig, ps = {}, {}
    start = time.time()
    for i in range(n_sims):
        d, t, f = generate(n, seed=1000 + i, null=null)
        R, _ = quiet_run(d, t, f)
        for x in R["tests"]:
            sig.setdefault(x["id"], []).append(x["significant"])
            ps.setdefault(x["id"], []).append(x.get("p", np.nan))
        if i == 4:
            est = (time.time() - start) / 5 * n_sims
            print(f"   (about {est / 60:.1f} minutes in total)", flush=True)
    return {k: (np.mean(v), np.mean(~np.isnan(ps[k]))) for k, v in sig.items()}, ps


def test_null_simulation(n_sims):
    print(f"4. Simulation with NO true effect: {n_sims} studies of 40 participants")
    rates, ps = simulate(n_sims, null=True)
    lo, hi = 0.05 - 2.6 * np.sqrt(0.05 * 0.95 / n_sims), 0.05 + 2.6 * np.sqrt(0.05 * 0.95 / n_sims)
    print(f"   acceptable range for a 5% test: {100 * lo:.1f}% to {100 * hi:.1f}%")
    print(f"   {'test':<6}{'significant':>12}{'tests run':>11}")
    for k in sorted(rates):
        r, share = rates[k]
        print(f"   {k:<6}{100 * r:>11.1f}%{100 * share:>10.0f}%")
        if k in ("S1",):
            check(r <= hi, f"{k}: false positive rate {100 * r:.1f}% should be at most 5% (exact test, conservative)")
        elif k == "S3b":
            continue
        else:
            check(lo <= r <= hi, f"{k}: false positive rate {100 * r:.1f}% should be about 5%")
    pooled = np.mean([rates[k][0] for k in rates if k not in ("S1",)])
    print(f"   average over tests (without S1): {100 * pooled:.1f}%")
    return rates


def test_power(n_sims=150):
    print(f"5. Sanity check WITH real effects: {n_sims} studies of 40 participants")
    rates, _ = simulate(n_sims, null=False)
    for k in ("P1", "S1", "S2", "S3", "C1a", "C1b"):
        print(f"   {k:<6}{100 * rates[k][0]:>6.0f}% significant")
    check(rates["P1"][0] > 0.4 and rates["C1a"][0] > 0.9 and rates["S3"][0] > 0.5,
          "with real effects the main tests detect them")


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        test_recount()
        test_typos(tmp)
        edge_cases(tmp)
    test_null_simulation(100 if quick else 1000)
    test_power(60 if quick else 150)
    print(f"\n{PASSES} checks passed, {len(FAILS)} failed")
    for m in FAILS:
        print("  FAILED:", m)
    sys.exit(1 if FAILS else 0)
