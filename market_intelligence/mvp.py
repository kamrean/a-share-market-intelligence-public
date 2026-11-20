"""Run the offline MVP demo with authored data and simulated delivery only."""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory, gettempdir

from . import scenario, workflow
from .models import Invalid, canonical, fingerprint
from .store import external_path


_LABEL = "OFFLINE SYNTHETIC DEMO; NOT INVESTMENT ADVICE"
_REASON = "Clarification: simulated acknowledgements are not real delivery."
_ERROR = "Offline demo blocked."
_EXCERPT_PREFIXES = (
    "mode=synthetic_test as_of=", "1. ", "News: ", "Event: ", "2. ", "Benchmark: ",
    "Synthetic breadth: ", "Turnover: ", "Flow: ", "3. ", "Sector: ", "Approved claim: ",
)


def _demo():
    # Check the temp parent before creating a workspace, including when TMP is overridden.
    temp_parent = external_path(Path(gettempdir()) / "p4mvp-workspace" / "workflow.sqlite").parent.parent
    with TemporaryDirectory(prefix="p4mvp-", dir=temp_parent) as temporary:
        directory = Path(temporary)
        result = workflow.run_case(scenario.example_case(), directory, "p4mvp-demo")
        if result["status"] != "complete":
            raise Invalid(_ERROR)
        report_id = result["report_id"]
        fingerprint(report_id)

        correction = workflow.correct_report(directory, report_id, _REASON)
        correction_id = correction["report_id"]
        fingerprint(correction_id)
        if (correction["status"] != "RECORDED_LOCAL_CORRECTION"
                or correction["supersedes"] != report_id or correction_id == report_id
                or correction["real_delivery"] != "not_attempted"):
            raise Invalid(_ERROR)

        output = directory / "report.txt"
        exported = workflow.export_report(directory, report_id, output)
        if exported != {"status": "EXPORTED_LOCAL_REPORT", "report_id": report_id}:
            raise Invalid(_ERROR)
        text = output.read_text(encoding="utf-8")

        delivery = workflow.simulate_delivery(directory, correction_id)
        if (delivery["status"] != "completed" or delivery["reason"] != "all_chunks_fake_sent"
                or delivery["label"] != "SIMULATED DELIVERY ONLY"
                or delivery["sink"] != "synthetic-local-sink" or delivery["report_id"] != correction_id):
            raise Invalid(_ERROR)
        return {
            "status": "COMPLETE_SYNTHETIC",
            "label": _LABEL,
            "mode": "synthetic_test",
            "report": {"report_id": report_id, "parts": 3, "text": text},
            "correction": {"report_id": correction_id, "supersedes": report_id, "reason": _REASON},
            "delivery": {
                "label": delivery["label"],
                "status": delivery["status"],
                "report_id": correction_id,
                "chunks": len(delivery["chunks"]),
                "real_delivery": "not_attempted",
            },
        }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid(_ERROR)


def _print_summary(result):
    report, correction, delivery = result["report"], result["correction"], result["delivery"]
    print(result["label"])
    print("Report " + report["report_id"] + " (excerpt)")
    # Take the first verified line of each kind, in publication order.
    remaining = set(_EXCERPT_PREFIXES)
    for line in report["text"].splitlines():
        prefix = next((p for p in remaining if line.startswith(p)), None)
        if prefix is not None:
            print(line)
            remaining.remove(prefix)
    print("\nLive benchmark coverage: 0/5. Semantic quality unassessed.")
    print(f'Correction {correction["report_id"]} supersedes {correction["supersedes"]}.')
    print(correction["reason"])
    print(f'{delivery["label"]}: {delivery["status"]} ({delivery["chunks"]} chunks).')


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", allow_abbrev=False).add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        result = _demo()
        if args.json:
            print(canonical(result))
        else:
            _print_summary(result)
        return 0
    except Exception:
        # The public command must not expose fixture, workspace, or store details.
        print(_ERROR, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
