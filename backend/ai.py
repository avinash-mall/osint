import os
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "http://localhost:8000/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "dummy")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-31B-it")

def get_ai_response(question: str) -> str:
    try:
        llm = ChatOpenAI(
            openai_api_base=OPENAI_API_BASE,
            openai_api_key=OPENAI_API_KEY,
            model_name=OPENAI_MODEL,
            temperature=0
        )
        
        graph = Neo4jGraph(url=URI, username=USERNAME, password=PASSWORD)
        
        chain = GraphCypherQAChain.from_llm(
            llm=llm,
            graph=graph,
            verbose=True,
            allow_dangerous_requests=True
        )
        
        result = chain.invoke({"query": question})
        return result["result"]
    except Exception as e:
        return f"Error connecting to AI or Database: {str(e)}"
