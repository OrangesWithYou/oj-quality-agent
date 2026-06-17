## Summary

- 

## Verification

- [ ] `python -m compileall -q agents apps tools run_cases_file.py judge.py`
- [ ] `python -B -c "import streamlit, langchain, langgraph, langfuse, quality_agent, quality_ui"`
- [ ] UI / CLI manually checked if relevant

## Notes

- Does this call external model APIs? Yes / No
- Does this change data formats? Yes / No
- Does this affect Langfuse tracing? Yes / No
