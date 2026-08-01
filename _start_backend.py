"""Start backend with correct event loop for Windows."""
import sys
sys.path.insert(0, "src")

from hoyabit_agent.config import load_dotenv, run_async
from hoyabit_agent.viz.server import serve

load_dotenv()
run_async(serve("127.0.0.1", 8000))
