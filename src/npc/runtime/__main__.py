"""Scenario dialogue preview; live network use requires --live."""

import argparse
import asyncio
import sys
from pathlib import Path

from npc.contracts.context import ContextScenario, PlayerInput, TurnInput
from npc.generation.config import ConfigurationFailure, load_provider
from npc.generation.provider import OfflineProvider
from npc.runtime.preview import PreviewLimits, PreviewRunner
from npc.rag import load_retriever


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--text", help="Override only the player's utterance in this test scenario")
    parser.add_argument("--live", action="store_true", help="Send this scenario's context to the configured API (may incur cost)")
    parser.add_argument("--config", type=Path, default=Path("configs/providers/openrouter.yaml"))
    parser.add_argument("--development-timeout-ms", type=int, help="Explicit longer test deadline; not a realtime benchmark")
    parser.add_argument("--rag-config", type=Path, help="Trusted source bindings for the existing RAG API")
    args = parser.parse_args()
    try:
        if args.scenario.stat().st_size > 256*1024:
            raise ValueError("scenario too large")
        scenario = ContextScenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
        if args.text is not None:
            turn_data = scenario.turn.model_dump()
            turn_data["player_input"] = PlayerInput(text=args.text).model_dump()
            scenario = ContextScenario(now=scenario.now, snapshot=scenario.snapshot, turn=TurnInput.model_validate(turn_data))
        limits = PreviewLimits() if args.development_timeout_ms is None else PreviewLimits(
            response_timeout_ms=args.development_timeout_ms,
            model_cutoff_ms=args.development_timeout_ms-550,
        )
        retriever = load_retriever(args.rag_config)
        if retriever:
            limits = limits.model_copy(update={"max_input_bytes": 10000,
                                               "retrieval_timeout_ms": 5000 if args.development_timeout_ms else 750})
        provider = load_provider(args.config) if args.live else OfflineProvider()
        if args.live:
            print("Live mode: supplied NPC/player context may leave this machine and incur API charges.", file=sys.stderr)
        async def run():
            try:
                return await PreviewRunner(provider, limits, retriever=retriever).run(scenario)
            finally:
                if hasattr(provider, "aclose"):
                    await provider.aclose()
        result = asyncio.run(run())
    except ConfigurationFailure as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("Invalid scenario/settings; check schema, scope, timestamps and limits. Private values omitted.", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(result.model_dump_json(indent=2))
    return 1 if result.status in {"error_fallback", "cancelled", "busy"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
