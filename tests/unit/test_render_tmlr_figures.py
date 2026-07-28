import re

from scripts.opd.render_tmlr_figures import build_tex_data


def test_figure_data_is_bound_to_released_results() -> None:
    rendered = build_tex_data()

    assert r"\newcommand{\RecoveryEligibleGenerations}{0}" in rendered
    assert r"\newcommand{\VOneDiverseSeedGroups}{0}" in rendered
    assert r"\newcommand{\VTwoBaseEffect}{23}" in rendered
    assert r"\newcommand{\VThreeRoundTwoEffect}{5.5}" in rendered
    assert r"\newcommand{\VTwoBaseValidAnyEffect}{37}" in rendered
    assert r"\newcommand{\VThreeRoundThreeValidAnyEffect}{31.5}" in rendered
    assert r"\newcommand{\RoutingVTwoFullPass}{0}" in rendered
    assert r"\newcommand{\RoutingVThreeFullPass}{9}" in rendered


def test_every_state_sign_partition_totals_twenty() -> None:
    rendered = build_tex_data()
    values = {}
    for line in rendered.splitlines():
        match = re.fullmatch(r"\\newcommand\{\\([^}]+)\}\{([^}]*)\}", line)
        if match is None:
            continue
        values[match.group(1)] = match.group(2)

    for version in ("VTwo", "VThree"):
        for snapshot in ("Base", "RoundTwo", "RoundThree"):
            total = sum(
                int(values[f"{version}{snapshot}{sign}"])
                for sign in ("Positive", "Zero", "Negative")
            )
            assert total == 20
