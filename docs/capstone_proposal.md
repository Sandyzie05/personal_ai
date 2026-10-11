# personal_ai: A Personal Assistant and Forecaster

## Project and intended user

For my capstone, I propose personal_ai, a multi-agent system with two core roles: Personal Assistant and Forecaster. Its intended user is an individual who wants help understanding personal records and researching financial uncertainty without surrendering control over sensitive information or decisions. I will be its first user.

Personal information is scattered across bills, statements, health exports, and conversations. Market information is similarly fragmented across prices, news, filings, and economic indicators. Answering a useful question often requires gathering evidence, checking dates, comparing sources, and remembering earlier corrections. personal_ai will coordinate these activities rather than merely generate plausible answers.

The Personal Assistant will retrieve and summarize authorized records, identify documented obligations, and retain approved preferences and task outcomes. The Forecaster will investigate stock-market scenarios and explain their evidence, limitations, and uncertainty. It will support research, not place trades or promise profitable predictions.

## Why an agentic system is necessary

A stand-alone language model cannot reliably know my current documents, fetch updated market evidence, perform reproducible calculations, or remember approved information across sessions. A single prompt also cannot adequately handle missing records, unavailable services, conflicting sources, or a task requiring several dependent actions.

An agentic system can select an appropriate tool, inspect its result, and decide whether additional evidence or clarification is needed. For example, it should check which statements exist before summarizing obligations, rather than inventing missing amounts. For forecasts, numerical calculations should come from data-processing tools; the language model should interpret evidence and communicate uncertainty.

## Environment and actions

The project will build on two existing repositories. personal_ai already provides a local interface, encrypted primary storage, document retrieval, and conversation history. stock_predictor provides market research, heuristic forecasts, specialist scenario reviews, and prediction-outcome tracking. These are foundations, not a completed multi-agent system.

The environment will include user-authorized documents, structured personal records, retrieved passages, approved task memories, historical prices, news, company filings, and economic indicators. Public market sources may require internet access, while private records and model processing will remain within an explicitly controlled local boundary.

A coordinator will delegate requests to the two core agents. Their tools will search documents, retrieve structured records, obtain market evidence, calculate summaries, compare scenarios, and store approved task outcomes. Independent forecast specialists will work in parallel on the same evidence packet, examining factors such as macroeconomic conditions, valuation, and downside risk. The coordinator will combine their findings while preserving sources and disagreements.

## Reasoning, memory, and feedback

I will use a bounded ReAct-style loop: select an action, observe the result, and revise the next step. Missing evidence may trigger another retrieval; ambiguous requests may require user clarification; tool failures may permit a limited retry. Step and time limits will prevent endless execution.

Retrieval-based memory will combine document knowledge with approved summaries of previous tasks. User corrections will update or supersede stored facts rather than leave contradictory memories unresolved.

For further reasoning, the system will compare a small number of alternative plans or bullish, base, and bearish scenarios against explicit evidence and risk criteria. It will expose concise decision rationales and tool outcomes, not private chain-of-thought.

Feedback will operate at several levels. Tool observations guide immediate next actions. User corrections guide future retrieval and preferences. Specialist disagreement triggers additional checking or lower confidence. Forecast outcomes, evaluated only after their horizons have elapsed, inform later assessment without leaking future information into earlier predictions.

## Safety and evaluation

Tools will be allowlisted, argument-validated, and read-only by default. Persistent changes will require approval. Trading, payments, arbitrary commands, and medical diagnosis are outside scope. Retrieved documents will be treated as evidence, never executable instructions. Private content will not be sent to public market services.

Existing plaintext retrieval-index storage remains a documented privacy limitation; I will not claim complete encryption. Evaluation will measure correct tool use, grounded answers, memory retrieval, bounded failure recovery, actual parallel delegation, and refusal of unauthorized actions. Forecasts will be compared with simple baselines using chronological, leakage-aware evaluation. Success means useful, accountable assistance under uncertainty, not guaranteed financial returns.
