# 金融资讯智能分析 Agent 平台最终设计文档（包含 RAG 架构）

Version: 1.1

Architecture: Event-Centric Multi-Agent Intelligence System + Hybrid RAG

------------------------------------------------------------------------

# 1. RAG 在整体系统中的定位

本系统采用：

**Event-Centric Hybrid RAG**

不是传统：

    Question
     ↓
    Vector Search
     ↓
    LLM

而是：

    User Query

    ↓

    Query Understanding

    ↓

    Event / Entity Understanding

    ↓

    Hybrid Retrieval

    ↓

    Context Fusion

    ↓

    Agent Reasoning

    ↓

    Investment Intelligence

核心检索来源：

-   Vector Database
-   Knowledge Graph
-   Structured Financial Database
-   Market Time Series Database

------------------------------------------------------------------------

# 2. RAG 总体架构

                      User Query

                           |

                  Query Understanding

                           |

              Entity + Intent Extraction

                           |

            --------------------------------

            |              |              |

       Vector RAG     Graph RAG     SQL RAG


            |              |              |

            --------------------------------

                           |

                  Context Fusion

                           |

                  LLM Reasoning

                           |

                      Final Answer

------------------------------------------------------------------------

# 3. Query Understanding

用户问题：

    为什么 NVIDIA 最近上涨？

首先转换为结构化任务：

``` json
{
 "intent":"stock_analysis",

 "entity":[
   {
    "name":"NVIDIA",
    "type":"company"
   }
 ],

 "time_range":"30_days",

 "tasks":[
   "find_events",
   "analyze_market_impact",
   "find_risk"
 ]
}
```

作用：

-   确定搜索目标
-   确定时间范围
-   决定调用哪些 Retrieval

------------------------------------------------------------------------

# 4. Vector RAG

## 4.1 Document Chunking

金融领域不推荐简单 Token 切分。

采用：

Event-based Chunking

例如：

    Article

     |
     +-- Event Description
     |
     +-- Financial Impact
     |
     +-- Market Reaction
     |
     +-- Background

------------------------------------------------------------------------

## 4.2 Embedding Metadata

每个 Chunk：

``` json
{
"id":"chunk_001",

"text":"NVIDIA released new GPU",

"company":"NVIDIA",

"event":"product_launch",

"time":"2026-07-20",

"source":"Reuters",

"importance":0.9
}
```

------------------------------------------------------------------------

# 5. Vector Database

推荐：

-   Milvus
-   Qdrant

Collection:

    financial_documents

Schema:

    id

    vector

    content

    company

    industry

    event_id

    timestamp

    source

    importance

------------------------------------------------------------------------

# 6. Knowledge Graph RAG

金融问题大量属于关系推理。

例如：

    AI Chip Demand Increase

            |

            v

    NVIDIA Revenue Growth

            |

            v

    Stock Price Impact

------------------------------------------------------------------------

## Graph Schema

节点：

    Company

    Person

    Product

    Technology

    Event

    Industry

    Market

关系：

    Company
     |
    released
     |
    Product


    Event
     |
    impact
     |
    Company


    Company
     |
    compete
     |
    Company

------------------------------------------------------------------------

# 7. Graph Retrieval 示例

问题：

    AI芯片需求增加影响哪些公司？

查询：

``` cypher
MATCH

(event:Event)
-[:IMPACT]->
(company:Company)

WHERE

event.type="AI_DEMAND"

RETURN company
```

结果：

    NVIDIA

    AMD

    TSMC

    ASML

------------------------------------------------------------------------

# 8. Structured RAG

金融分析必须结合结构化数据。

数据：

-   股票价格
-   财务指标
-   财报
-   PE Ratio
-   Revenue
-   Margin

数据库：

    PostgreSQL

    TimescaleDB

例如：

分析 Apple：

    News:

    China demand weakness


    Market:

    Stock -8%


    Financial:

    Revenue slowdown

------------------------------------------------------------------------

# 9. Context Fusion

三类 Retrieval：

    Vector Result

    Graph Result

    Structured Data Result

统一：

``` json
{
"context":{

"documents":[],

"relations":[],

"metrics":[]

}
}
```

然后输入 LLM。

------------------------------------------------------------------------

# 10. Agentic RAG

Agent 自动决定检索路径。

例如：

问题：

    分析 Tesla 风险

Planner：

    Need:

    1. Recent events

    2. Competition

    3. Financial status

    4. Regulation

调用：

    Research Agent

     |
     +-- Vector Search

     |
     +-- Graph Query

     |
     +-- Market API

     |
     +-- Regulation DB

------------------------------------------------------------------------

# 11. Reranking

避免直接使用 Top-K。

流程：

    1000 Candidates

            |

    Embedding Retrieval

            |

    Top 50

            |

    Reranker

            |

    Top 5

推荐：

-   BGE Reranker
-   Cohere Rerank
-   Cross Encoder

------------------------------------------------------------------------

# 12. Time-aware RAG

金融系统需要考虑：

    Similarity

    +

    Recency

    +

    Importance

    +

    Source Credibility

评分：

    Final Score =

    0.5 Semantic Similarity

    +

    0.2 Recency

    +

    0.2 Importance

    +

    0.1 Source Weight

------------------------------------------------------------------------

# 13. RAG 服务架构

    API Gateway

            |

    Retrieval Service

            |

    --------------------------------

    |              |              |

    Vector       Graph          SQL

    Search       Query          Query


            |

    Context Builder

            |

    LLM Service

------------------------------------------------------------------------

# 14. 推荐技术栈

## Embedding

-   BGE-large
-   E5-large
-   GTE

## Vector DB

-   Milvus

## Knowledge Graph

-   Neo4j

## Reranker

-   BGE-reranker

## Agent Framework

-   LangGraph

------------------------------------------------------------------------

# 15. 完整金融 RAG Pipeline

    User Question

          |

    Query Understanding

          |

    Entity Extraction

          |

    Intent Planning

          |

    --------------------------------

    |              |               |

    Vector RAG   Graph RAG    SQL RAG


    |              |               |

    --------------------------------

                 |

            Reranking

                 |

           Context Fusion

                 |

           LLM Reasoning

                 |

           Citation Answer

------------------------------------------------------------------------

# 16. 与 Event Intelligence 的结合

普通 RAG：

    Document

    ↓

    Vector

    ↓

    Answer

本系统：

    Document

    ↓

    Event Extraction

    ↓

    Event Graph

    ↓

    Event Memory

    ↓

    RAG Retrieval

    ↓

    Agent Reasoning

    ↓

    Investment Insight

核心创新：

> RAG 检索的不只是文章，而是影响市场的事件链。

------------------------------------------------------------------------

# 17. 下一阶段 LLD

继续设计：

1.  Milvus Collection Schema
2.  Neo4j Event Graph Schema
3.  Chunking Pipeline
4.  Embedding Service
5.  Retrieval API
6.  LangGraph Workflow
7.  Prompt Template
8.  Context Window Management
9.  Real-time Update Pipeline
