"""Tsuzuri research agent entry point.

Configuration and mode selection only; the research runtime lives in
tsuzuri.research (see tsuzuri/research/pipeline.py for the mode
orchestration).
"""

import asyncio

from dotenv import load_dotenv

from tsuzuri.research.models import ResearchMode
from tsuzuri.research.pipeline import run_research

load_dotenv()

question = (
    "请确认原版 STEINS;GATE 是什么时候登陆 Steam 的。"
    "如果有必要请尝试交叉核对 SteamDB。"
    "必须基于工具结果回答。"
)

mode = ResearchMode.GROUNDED


async def main() -> None:
    result = await run_research(
        question=question,
        mode=mode,
    )

    if result.final_answer is not None:
        print(result.final_answer)
    else:
        print("(research stopped; see report)")

    print()
    print(result.usage)
    print()
    print(f"Research report: {result.report_path}")


if __name__ == "__main__":
    asyncio.run(main())
