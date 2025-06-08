# scout.py
from typing import List
import feedparser
import hashlib

ARXIV_API = "http://export.arxiv.org/api/query?search_query=all:{}&start=0&max_results={}"

def arxiv_search(topic: str, max_results: int = 10) -> List[dict]:
    url = ARXIV_API.format(topic.replace(" ", "+"), max_results)
    feed = feedparser.parse(url)
    papers = []
    for entry in feed.entries:
        paper_id = hashlib.md5(entry.id.encode()).hexdigest()
        papers.append({
            "id": f"arxiv_{paper_id}",
            "topic": topic,
            "type": "paper",
            "url": entry.id,
            "year": int(entry.published.split("-")[0]),
            "title": entry.title,
            "summary": entry.summary
        })
    return papers

test = """
if __name__ == "__main__":
    topic = "heart morphogenesis"
    papers = arxiv_search(topic, max_results=5)
    for p in papers:
        print(f"[{p['year']}] {p['title']} → {p['url']}")

"""