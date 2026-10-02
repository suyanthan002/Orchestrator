from orchestrator.parser import parse_cline_output


sample_output = """
{"type":"system","message":"Cline started"}
{"type":"tool_call","tool":"read_files"}
{"type":"tool_result","tool":"read_files"}
"""


result = parse_cline_output(sample_output)


print("Success:", result.success)
print("Final response:", result.final_response)
print("Finish reason:", result.finish_reason)
print("Iterations:", result.iterations)
print("Usage:", result.usage)