
try:
    from langchain_core.prompts import ChatPromptTemplate
    print("langchain_core.prompts imported successfully")
except ImportError:
    print("langchain_core.prompts failed")

try:
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    print("langchain_core.messages imported successfully")
except ImportError:
    print("langchain_core.messages failed")

try:
    from langchain.prompts import ChatPromptTemplate
    print("langchain.prompts imported successfully")
except ImportError:
    print("langchain.prompts failed")
