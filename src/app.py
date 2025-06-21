from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_community.utilities import SQLDatabase
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq
from datetime import datetime
import streamlit as st
import urllib.parse

# -------------------- Init DB -----------------------
def init_database(user: str, password: str, host: str, port: str, database: str) -> SQLDatabase:
    encoded_password = urllib.parse.quote(password)
    db_uri = f"mysql+mysqlconnector://{user}:{encoded_password}@{host}:{port}/{database}"
    return SQLDatabase.from_uri(db_uri)

# ------------------ SQL Generation Chain ---------------------
def get_sql_chain(db):
    template = """
    You are a MySQL expert. Generate ONLY the SQL query for this request:
    {question}
    
    Database Schema:
    {schema}
    
    Conversation History:
    {chat_history}
    
    Rules:
    1. Return ONLY the SQL query
    2. No explanations
    3. No markdown
    4. No backticks
    5. Use exact column/table names from schema
    
    SQL Query:
    """
    
    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(model="llama3-8b-8192", temperature=0)

    return (
        RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
        | prompt
        | llm
        | StrOutputParser()
    )

# ------------------ Query Validation ---------------------
def validate_query(db: SQLDatabase, query: str) -> str:
    """Validate and correct SQL queries"""
    if not query.strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
        return query
    
    validation_prompt = """
    Validate this MySQL query against the schema. Return ONLY the corrected SQL or the original if valid:
    
    Schema: {schema}
    
    Query: {query}
    
    Rules:
    1. If valid, return the exact same query
    2. If invalid, return the corrected query
    3. NEVER return commentary like "The query is valid"
    4. NEVER return markdown or code blocks
    """
    
    prompt = ChatPromptTemplate.from_template(validation_prompt)
    llm = ChatGroq(model="llama3-8b-8192", temperature=0)
    
    chain = (
        RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
        | prompt
        | llm
        | StrOutputParser()
    )
    
    try:
        validated = chain.invoke({"query": query})
        if "valid" in validated.lower() or not validated.strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
            return query
        return validated
    except Exception:
        return query

# ------------------ Response Generator ---------------------
def sanitize_query(query: str) -> str:
    query = query.strip()
    for prefix in ["```sql", "```"]:
        if query.startswith(prefix):
            query = query[len(prefix):].strip()
    if query.endswith("```"):
        query = query[:-3].strip()
    return query

def get_response(user_query: str, db: SQLDatabase, chat_history: list, show_debug=False):
    response_template = """
You are a helpful SQL analyst assistant. Convert database results into clear, accurate responses.

### Strict Guidelines:
1. ONLY use data present in the SQL Response
2. For empty results: "No matching records found."
3. Structure responses based on the returned columns
4. Never invent or assume data not in results
5. For lists, use bullet points
6. For counts, state the exact number
7. For comparisons, highlight differences

### Response Examples:
- For student records: 
  "Found 3 students:"
  "• Riya Mehra (CS) - Data Structures (Prof. Sharma)"
  "• Aman Jain (ME) - Thermodynamics (Prof. Mehta)"
  
- For empty results:
  "No professors found teaching in Spring 2025"

- For counts:
  "5 students scored above 90%"

### Database Schema:
{schema}

### Conversation Context:
{chat_history}

### Current Task:
User Question: {question}

### Executed SQL:
{query}

### SQL Results:
{response}

Now provide the CLEAR, CONCISE natural language response:
"""

    try:
        generated_query = get_sql_chain(db).invoke({
            "question": user_query,
            "chat_history": chat_history
        })
        generated_query = sanitize_query(generated_query)
        
        validated_query = validate_query(db, generated_query)
        query_results = db.run(validated_query)

        debug_entry = {
            "timestamp": datetime.now().isoformat(),
            "question": user_query,
            "generated_query": generated_query,
            "validated_query": validated_query,
            "results": query_results
        }
        if 'query_history' not in st.session_state:
            st.session_state.query_history = []
        st.session_state.query_history.append(debug_entry)
        
        prompt = ChatPromptTemplate.from_template(response_template)
        llm = ChatGroq(model="llama3-8b-8192", temperature=0)
        
        response_chain = (
            prompt
            | llm
            | StrOutputParser()
        )
        
        final_response = response_chain.invoke({
            "question": user_query,
            "chat_history": chat_history,
            "query": validated_query,
            "response": query_results,
            "schema": db.get_table_info()
        })
        
        return final_response
        
    except Exception as e:
        error_type = type(e).__name__
        
        if "ProgrammingError" in error_type:
            return f"Database error: {str(e)}. Please verify your query syntax."
        elif "ConnectionError" in error_type:
            return "Failed to connect to database. Please check your connection settings."
        elif "EmptyResult" in str(e):
            return "No results found matching your criteria."
        else:
            st.error(f"Full error details: {str(e)}")
            return "Sorry, I encountered an unexpected error. The technical details have been logged."

# -------------------- Streamlit UI -----------------------
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        AIMessage(content="Hello! I'm a SQL assistant. Ask me anything about your database."),
    ]

load_dotenv()

st.set_page_config(page_title="Chat with MySQL", page_icon=":speech_balloon:")
st.title("🗃️ Chat with MySQL")

with st.sidebar:
    st.subheader("Settings")
    st.write("Connect to your MySQL database to begin.")
    
    st.text_input("Host", value="localhost", key="Host")
    st.text_input("Port", value="3306", key="Port")
    st.text_input("User", value="root", key="User")
    st.text_input("Password", type="password", value="utkarsh@123", key="Password")
    st.text_input("Database", value="university_management", key="Database")

    show_debug = st.checkbox("Show SQL Response", value=True)

    if st.button("Connect"):
        with st.spinner("Connecting to database..."):
            db = init_database(
                st.session_state["User"],
                st.session_state["Password"],
                st.session_state["Host"],
                st.session_state["Port"],
                st.session_state["Database"]
            )
            st.session_state.db = db
            st.success("✅ Connected to database!")

# ------------------ Chat History UI ---------------------
for message in st.session_state.chat_history:
    if isinstance(message, AIMessage):
        with st.chat_message("AI"):
            st.markdown(message.content)
    elif isinstance(message, HumanMessage):
        with st.chat_message("Human"):
            st.markdown(message.content)

# ------------------ User Chat Input ---------------------
user_query = st.chat_input("Type a message...")

if user_query is not None and user_query.strip() != "":
    st.session_state.chat_history.append(HumanMessage(content=user_query))

    with st.chat_message("Human"):
        st.markdown(user_query)

    with st.chat_message("AI"):
        if "db" not in st.session_state:
            st.error("Please connect to a database first")
            st.stop()
            
        response = get_response(
            user_query, 
            st.session_state.db, 
            st.session_state.chat_history, 
            show_debug=show_debug
        )
        st.markdown(response)

    st.session_state.chat_history.append(AIMessage(content=response))

# ------------------ Persistent SQL Response Display ---------------------
if show_debug and 'debug_info' in st.session_state and st.session_state.debug_info:
    st.subheader("📊 SQL Response")
    
    latest_debug = st.session_state.debug_info[-1]
    
    with st.expander("View latest query details"):
        st.text("Query Explanation:")
        st.write(latest_debug["explanation"])
        st.code(latest_debug["query"], language="sql")
        if isinstance(latest_debug["response"], list):
            st.dataframe(latest_debug["response"])
        else:
            st.write(latest_debug["response"])
    
    if len(st.session_state.debug_info) > 1:
        with st.expander("View query history"):
            for i, debug in enumerate(reversed(st.session_state.debug_info)):
                st.markdown(f"**Query {len(st.session_state.debug_info)-i}**: {debug['question']}")
                st.code(debug["query"], language="sql")
                st.write("---")
