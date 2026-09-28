"""DataCrew 应用包。

分层原则（整洁架构，依赖只能向内）：
    api → application → domain ← infra
    agents/tools 属于 application 与 domain 的协作层
外部可替换组件（LLM、向量库、对象存储）全部经 infra 的适配器接入。
"""