"""Markdown tables for reports/T07 from the job's checks.json.

Usage: python report_tables.py checks.json > tables.md
"""

import json
import sys


def fmt(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}"


def main(path):
    c = json.load(open(path))
    cov = c["coverage"]
    out = []
    out.append("### Coverage\n")
    out.append("| | pixels |\n|---|---|")
    for k in ("walkable_px", "walkable_in_tiles", "walkable_outside_tiles", "walkable_in_aoi_outside_tiles",
              "walkable_outside_aoi", "aoi_px_outside_tiles"):
        out.append(f"| `{k}` | {cov[k]:,} |")
    out.append(f"\nTiles: {cov['tiles_done']} of {cov['tiles_planned']}; missing: {cov['missing_tiles'] or 'none'}. "
               f"Walkable pixels outside the tiles by T03 category: {cov['by_category_outside_tiles']}.\n")

    out.append("### Sanity per step (walkable pixels)\n")
    out.append("| Local | Ta | Tmrt−Ta p1 / p50 / p99 (min, max) | out of range | UTCI NaN | UTCI mean | UTCI sun | UTCI tree shade | UTCI bldg shade | Tmrt sun | Tmrt bldg shade | sunlit % |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in c["sanity"]:
        d = r["tmrt_minus_ta"]
        sun_pct = "–" if r["step"] in c["night_bands"] else f"{100 * r['share_sunlit']:.0f}"
        out.append(f"| {r['label']} | {r['ta']:.1f} | {d['p1']:.1f} / {d['p50']:.1f} / {d['p99']:.1f} ({d['min']:.1f}, {d['max']:.1f}) | "
                   f"{r['n_out_of_range']} | {r['utci_nan']} | {r['utci']['mean']:.1f} | {fmt(r.get('utci_sunlit'))} | "
                   f"{fmt(r.get('utci_tree_shade'))} | {fmt(r.get('utci_bldg_shade'))} | {fmt(r.get('tmrt_sunlit'))} | "
                   f"{fmt(r.get('tmrt_bldg_shade'))} | {sun_pct} |")
    out.append(f"\nWind input range (UTCI): {c['wind_utci_range']} m/s.\n")

    out.append("### Far casters: T02 building shade where SOLWEIG has sun\n")
    out.append("| Local | sun el ° | T02 bldg shade % | SOLWEIG full shade % | T02 shade, SOLWEIG sun: px | % of walkable | % of T02 shade | SOLWEIG shade, T02 none % | ΔUTCI sun−shade K | mean UTCI excess K |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in c["far_casters"]:
        out.append(f"| {r['label']} | {float(r['el']):.1f} | {100 * r['t02_bldg_shade']:.1f} | {100 * r['solweig_full_shade']:.1f} | "
                   f"{r['t02_shade_solweig_sun']:,} | {100 * r['share_t02_shade_solweig_sun']:.2f} | "
                   f"{100 * r['share_of_t02_shade_missed']:.2f} | {100 * r['share_solweig_shade_t02_no_bldg']:.2f} | "
                   f"{fmt(r['utci_sun_minus_shade'])} | {fmt(r['utci_excess_mean_over_walkable'], 3)} |")
    att = c.get("far_caster_attribution")
    if isinstance(att, list) and att:
        out.append("\n**Where is T02's caster?** Sample of missed pixels, ray-marched towards the sun on T01 dsm + T02 far-caster surface:\n")
        out.append("| Local | missed px | sample | dist p10 / p50 / p90 m | ≤ 200 m | 200–500 m | 500–1000 m | > 1000 m | beyond tile halo | none found | caster height p50 m |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in att:
            d = r["dist_p10_p50_p90"] or [None] * 3
            out.append(f"| {r['label']} | {r['n_missed']:,} | {r['n_sample']} | {' / '.join(fmt(x, 0) for x in d)} | "
                       f"{100 * r['share_le_200m']:.0f} % | {100 * r['share_200_500m']:.0f} % | {100 * r['share_500_1000m']:.0f} % | "
                       f"{100 * r['share_gt_1000m']:.0f} % | {100 * r['share_beyond_halo']:.0f} % | {100 * r['no_caster_found']:.0f} % | "
                       f"{fmt(r['caster_height_above_px_p50'])} |")
    elif att:
        out.append(f"\nAttribution: {att}\n")

    worst = {}
    for r in c["far_casters"]:
        for t, v in r["per_tile"].items():
            if r["label"] >= "19:00":
                worst[t] = worst.get(t, 0) + v
    out.append("\nMissed pixel-steps 19:00–20:30 by tile (top 8): " +
               ", ".join(f"{t} {v:,}" for t, v in sorted(worst.items(), key=lambda x: -x[1])[:8]) + "\n")

    out.append("### Tile seams\n")
    out.append("| Edge | orient. | halos | Tmrt MAE across / inside (max over steps) K | max |mean jump| K | shadow flips across / inside (max) |")
    out.append("|---|---|---|---|---|---|")
    for r in c["seams"]:
        out.append(f"| {r['a']} · {r['b']} | {r['orientation'][0]} | {r['halo_a']}/{r['halo_b']} | "
                   f"{max(r['tmrt_mae_across']):.2f} / {max(r['tmrt_mae_inside']):.2f} | {max(abs(x) for x in r['tmrt_mean_jump']):.2f} | "
                   f"{max(r['shadow_flip_across']):.3f} / {max(r['shadow_flip_inside']):.3f} |")

    p = c["pilot"]
    if isinstance(p, list):
        out.append("\n### Pilot vs district\n")
        out.append("| Local | n | Tmrt bias K | MAE K | p99 |Δ| K | share |Δ| > 1 K | shadow agreement |")
        out.append("|---|---|---|---|---|---|---|")
        for r in p:
            if r["label"] in ("06:00", "09:00", "13:00", "17:00", "19:00", "20:00", "20:30", "21:00", "22:00"):
                out.append(f"| {r['label']} | {r['n']:,} | {r['tmrt_bias']:.3f} | {r['tmrt_mae']:.3f} | {fmt(r['tmrt_p99_abs'], 3)} | "
                           f"{fmt(r['share_abs_gt_1K'], 4)} | {fmt(r['shadow_agree'], 4)} |")
    print("\n".join(out))


if __name__ == "__main__":
    main(*sys.argv[1:])
