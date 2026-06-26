"""
CLI chat interface for the LangGraph matching agent.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage

from matching_agent import MatchingAgent


DEMO_SCENARIOS = [
    {
        "name": "Full pipeline with refinement",
        "turns": [
            "Match candidates for data/job_descriptions/job_01.json",
            "Prioritize TensorFlow and machine learning experience over general backend skills",
            "done",
        ],
    },
    {
        "name": "Conversational skill search",
        "turns": [
            "Find me candidates with React and 3+ years experience",
        ],
    },
    {
        "name": "Side-by-side comparison",
        "turns": [
            "Match candidates for data/job_descriptions/job_01.json",
            "Compare the top 3 matches side by side",
        ],
    },
    {
        "name": "Ranking explanation",
        "turns": [
            "Find me candidates with React and 3+ years experience",
            "Why did the top candidate rank higher than the second candidate?",
        ],
    },
    {
        "name": "Interview questions",
        "turns": [
            "Match candidates for data/job_descriptions/job_01.json",
            "Generate screening interview questions for the top candidate",
        ],
    },
]


def _is_feedback_response(user_input: str) -> bool:
    lowered = user_input.lower()
    feedback_markers = (
        "done",
        "finish",
        "next round",
        "round 2",
        "round 3",
        "deep",
        "prioritize",
        "instead",
        "more weight",
        "less weight",
        "refine",
        "adjust",
    )
    return any(marker in lowered for marker in feedback_markers)


def _print_banner() -> None:
    print("=" * 72)
    print("LangGraph Candidate Matching Agent")
    print("=" * 72)
    print("Commands: 'exit' or 'quit' to leave, 'help' for examples")
    print()


def _print_help() -> None:
    print("Example queries:")
    print("- Match candidates for data/job_descriptions/job_01.json")
    print("- Find me candidates with React and 3+ years experience")
    print("- Compare the top 3 matches side by side")
    print("- Why did John rank higher than Jane?")
    print("- Generate screening interview questions for the top candidate")
    print("- Prioritize cloud experience and rerun ranking")
    print()


def _extract_ai_messages(update: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    for value in update.values():
        if not isinstance(value, dict):
            continue
        messages = value.get("messages", [])
        for message in messages:
            if isinstance(message, AIMessage):
                texts.append(str(message.content))
    return texts


def _latest_state_values(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if "values" in snapshot:
        return snapshot["values"]
    return snapshot


def _run_turn(
    agent: MatchingAgent,
    config: Dict[str, Any],
    payload: Any,
    *,
    allow_interrupt: bool = True,
) -> Dict[str, Any]:
    interrupted = False

    for event in agent.graph.stream(payload, config=config, stream_mode="updates"):
        if "__interrupt__" in event:
            interrupted = True
            interrupt_payload = event["__interrupt__"]
            if interrupt_payload:
                prompt = interrupt_payload[0].value.get("prompt", "Provide feedback to continue.")
                print("\n--- Human Feedback Loop ---")
                print(prompt)
            continue

        for node_name, update in event.items():
            for text in _extract_ai_messages({node_name: update}):
                print(f"\n[{node_name}]")
                print(text)

    snapshot = agent.graph.get_state(config)
    state_values = _latest_state_values(snapshot)
    next_nodes = snapshot.next if hasattr(snapshot, "next") else ()

    if allow_interrupt and (interrupted or next_nodes):
        interrupted = True
        if not next_nodes:
            print("\nProvide feedback to continue, or type 'done' to finish.")
        print("Options: refine requirements | 'next round' for deep screening | 'done'")

    return {
        "state": state_values,
        "interrupted": interrupted,
        "next_nodes": list(next_nodes),
    }


def run_interactive(agent: MatchingAgent, thread_id: Optional[str] = None) -> None:
    _print_banner()
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    pending_interrupt = False
    last_state: Dict[str, Any] = {}

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye.")
            return
        if user_input.lower() == "help":
            _print_help()
            continue

        if pending_interrupt and _is_feedback_response(user_input):
            payload = agent.resume_feedback(user_input)
            result = _run_turn(agent, config, payload, allow_interrupt=True)
        else:
            payload = agent.initial_state(user_input)
            result = _run_turn(agent, config, payload, allow_interrupt=True)

        last_state = result.get("state", {})
        pending_interrupt = bool(result.get("interrupted"))


def run_demo(agent: MatchingAgent, scenario_index: Optional[int] = None) -> None:
    scenarios = DEMO_SCENARIOS
    if scenario_index is not None:
        if scenario_index < 0 or scenario_index >= len(scenarios):
            print(f"Invalid scenario index. Choose 0-{len(scenarios) - 1}.")
            sys.exit(1)
        scenarios = [scenarios[scenario_index]]

    for scenario in scenarios:
        print("\n" + "#" * 72)
        print(f"DEMO: {scenario['name']}")
        print("#" * 72)

        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        last_state: Dict[str, Any] = {}
        pending_interrupt = False

        for turn in scenario["turns"]:
            print(f"\nYou> {turn}")
            if pending_interrupt and _is_feedback_response(turn):
                payload = agent.resume_feedback(turn)
            else:
                payload = agent.initial_state(turn)

            result = _run_turn(agent, config, payload, allow_interrupt=True)
            last_state = result.get("state", {})
            pending_interrupt = bool(result.get("interrupted"))


def list_demo_scenarios() -> None:
    print("Available demo scenarios:")
    for idx, scenario in enumerate(DEMO_SCENARIOS):
        print(f"  {idx}. {scenario['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph candidate matching chat interface")
    parser.add_argument("--demo", action="store_true", help="Run built-in demo conversation flows")
    parser.add_argument("--scenario", type=int, help="Run a single demo scenario by index")
    parser.add_argument("--list-scenarios", action="store_true", help="List demo scenarios")
    parser.add_argument("--thread-id", type=str, help="Optional conversation thread id")
    args = parser.parse_args()

    agent = MatchingAgent()

    if args.list_scenarios:
        list_demo_scenarios()
        return

    if args.demo or args.scenario is not None:
        run_demo(agent, scenario_index=args.scenario)
        return

    run_interactive(agent, thread_id=args.thread_id)


if __name__ == "__main__":
    main()
