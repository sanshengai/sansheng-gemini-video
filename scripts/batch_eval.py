#!/usr/bin/env python
"""批量素材验收(头号用例):扫一个文件夹的视频,逐段 screening 评分,汇总成选用决策表。
串行处理(避免并发撞配额/race);每段独立 JSON + 一个 _summary.json + 控制台表格。
最终把 _summary.json 交给 Claude 做横向选用决策。"""
import argparse
import fnmatch
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_video import analyze_one, DEFAULT_RUBRIC

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".3gp", ".mpeg", ".mpg"}


def main():
    ap = argparse.ArgumentParser(description="批量视频素材验收")
    ap.add_argument("folder", help="录屏/素材文件夹")
    ap.add_argument("--rubric", default=None, help="验收标准文件(每行一条),留空用内置通用档")
    ap.add_argument("--pattern", default="*", help="文件名匹配(fnmatch),默认所有视频")
    ap.add_argument("--out-dir", default=None, help="结果目录,默认 <folder>/_eval")
    ap.add_argument("--fps", type=float, default=None,
                    help="采样帧率(最大省钱杠杆),录屏建议 0.2-0.5、口播 1.0;留空走 Gemini 原生帧率")
    ap.add_argument("--media-resolution", choices=["low", "high"], default="low")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"不是文件夹: {folder}")
    vids = sorted(p for p in folder.iterdir()
                  if p.suffix.lower() in VIDEO_EXT and fnmatch.fnmatch(p.name, args.pattern))
    if not vids:
        raise SystemExit(f"{folder} 里没有匹配的视频")

    rubric = DEFAULT_RUBRIC
    if args.rubric:
        if not Path(args.rubric).is_file():
            raise SystemExit(f"--rubric 文件不存在: {args.rubric}"
                             "(传了 --rubric 就必须能读到;留空才走内置通用档,不静默回退)")
        rubric = [ln.strip() for ln in Path(args.rubric).read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]

    out_dir = Path(args.out_dir) if args.out_dir else folder / "_eval"
    out_dir.mkdir(exist_ok=True)

    print(f"[批量验收] {len(vids)} 段视频,标准 {len(rubric)} 条,结果落 {out_dir}")
    rows = []
    total_cost = 0.0
    for i, p in enumerate(vids, 1):
        print(f"  ({i}/{len(vids)}) {p.name} ...", end=" ", flush=True)
        try:
            res = analyze_one(str(p), intent="screening", rubric=rubric,
                              fps=args.fps, media_resolution=args.media_resolution)
            (out_dir / f"{p.stem}.eval.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            a = res.get("analysis", {})
            cost = res["usage"]["est_cost_usd"]
            total_cost += cost
            rows.append({"file": p.name, "usable": a.get("usable"),
                         "overall_score": a.get("overall_score"),
                         "top_issues": a.get("top_issues", []),
                         "suggested_action": a.get("suggested_action", ""), "cost": cost})
            print(f"OK usable={a.get('usable')} score={a.get('overall_score')} ${cost}")
        except Exception as e:
            rows.append({"file": p.name, "error": str(e)[:200]})
            print(f"FAIL {str(e)[:80]}")

    summary = {"folder": str(folder), "rubric": rubric, "count": len(vids),
               "total_cost_usd": round(total_cost, 4), "results": rows}
    (out_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 验收汇总 ===")
    for r in rows:
        if "error" in r:
            print(f"  [ERR] {r['file']}: {r['error'][:50]}")
        else:
            mark = "可用" if r["usable"] else "不可用"
            issues = "; ".join(r["top_issues"][:2]) if r["top_issues"] else "-"
            print(f"  [{mark}] score={r['overall_score']} {r['file']}  问题: {issues[:50]}")
    print(f"\n总成本 ${total_cost:.4f}  汇总 -> {out_dir / '_summary.json'}")
    print("把 _summary.json 交给 Claude 做最终横向选用决策(用哪几段/哪些重录)。")


if __name__ == "__main__":
    main()
