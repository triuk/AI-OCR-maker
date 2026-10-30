# Call Claude through the subscription CLI (`claude -p`), not an API key

AI requests go through the user's Claude subscription via the `claude -p` CLI, the same way as in ai-asr-sub-maker, never through a billed API key. Codex may be offered as an alternative provider behind the same interface. This keeps running costs predictable and lets the CLI's built-in image input and web search serve reconstruction and web lookup, at the price of depending on CLI behaviour and subscription usage limits.
