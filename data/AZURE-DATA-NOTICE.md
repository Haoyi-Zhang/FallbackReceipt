# Azure public trace excerpts notice

This artifact retains two small deterministic excerpts from Microsoft Azure's
`Azure/AzurePublicDataset` repository:

- `azure_llm_code_excerpt.csv`: the header and first 128 data rows of
  `data/AzureLLMInferenceTrace_code.csv` (upstream blob identifier
  `62bd8109d0f2b1bc48206d397ecca48d8de03aab`);
- `azure_llm_conv_excerpt.csv`: the header and first 128 data rows of
  `data/AzureLLMInferenceTrace_conv.csv` (upstream blob identifier
  `5b9219f2caf93bbbf95ec29894e206115658e235`).

The upstream repository describes its public trace data as available under a CC
BY attribution license. Both files record timestamps, context-token counts, and
generated-token counts for samples of Azure LLM inference invocations.

Source documentation and files:

- https://github.com/Azure/AzurePublicDataset
- https://github.com/Azure/AzurePublicDataset/blob/master/data/AzureLLMInferenceTrace_code.csv
- https://github.com/Azure/AzurePublicDataset/blob/master/data/AzureLLMInferenceTrace_conv.csv
- https://github.com/Azure/AzurePublicDataset/blob/master/LICENSE

The excerpts are redistributed only as small, deterministic research inputs with
attribution. The code-trace excerpt freezes the empirical tercile thresholds in
`src/workload.py`; the conversation-trace excerpt is processed by the same
mapping without per-source refitting. The mapping produces bounded ordinal score,
cost, and duration envelopes for a finite stress model. It does not use or claim
Azure latency measurements, resource prices, safety labels, model confidence,
model quality, or production service-level objectives.
