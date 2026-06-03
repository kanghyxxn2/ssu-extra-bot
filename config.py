import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "ssu_extra.db")
SCRAPE_INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "6"))

SSU_JOB_LIST_URL = "https://job.ssu.ac.kr/service/careerProgram/careerProgramList.do"
SSU_JOB_DETAIL_URL = "https://job.ssu.ac.kr/service/careerProgram/careerProgramView.do"
CATEGORIES = [
    "상담/멘토링/코칭",
    "공모전/경진대회",
    "소모임/동아리",
    "특강/워크숍",
    "서포터즈/홍보대사",
    "전공탐색프로그램",
    "진로탐색프로그램",
    "채용설명회/채용상담",
    "공공인재양성반",
    "기타",
]

CATEGORY_EMOJI = {
    "상담/멘토링/코칭": "🤝",
    "공모전/경진대회": "🏆",
    "소모임/동아리": "👥",
    "특강/워크숍": "📚",
    "서포터즈/홍보대사": "📣",
    "전공탐색프로그램": "🔬",
    "진로탐색프로그램": "🧭",
    "채용설명회/채용상담": "💼",
    "공공인재양성반": "🏛",
    "기타": "📌",
}

STATUS_EMOJI = {
    "모집중": "🟢",
    "모집대기": "🟡",
    "종료": "🔴",
    "분반모집": "🔵",
}
