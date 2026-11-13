# AI configuration

AI is optional. It helps in these places:

1. **Item identification**: suggest name, description, category, manufacturer and model from a photo.
2. **Container, shelf and area contents**: list the distinct items and quantities visible in a photo of an open box, a shelf or a whole storage area (with its layout).
3. **Documents**: when a document is added to an item, work out what it is (receipt, warranty, manual...) and copy out what's printed for the item's fields (purchase date, price, shop, warranty, make, model, serial number). It runs in the background, and you choose what to apply.
4. **Equipment manuals**: read the maintenance schedule, the parts and consumables, and what each service needs, from a manual's PDF text or from photos of its pages.
5. **Search interpretation**: turn a question into search filters.

AI output is **never** saved without review. Identification only pre-fills the form. Container suggestions wait in a review screen. Search answers are always built from database rows: the AI chooses *what to look for*, never *where things are* or *how many*.

## Providers

| `ai_provider` | Cost | Key needed | Notes |
|---|---|---|---|
| `home_assistant` (default) | Whatever your HA AI integration costs. The Google Gemini free tier works. | No | Uses Home Assistant's **AI Task** (`ai_task.generate_data`) with the model you already set up, e.g. *Google Generative AI* (`ai_task.google_ai_task`). Images go through `/media` as a media-source attachment. |
| `gemini` | Free tier available | `ai_api_key` from Google AI Studio | Direct REST call. Default model `gemini-2.5-flash`. |
| `ollama` | Free, local | No | Local vision model, e.g. `qwen2.5vl:7b`. Set `ai_base_url` (e.g. `http://192.168.1.20:11434/v1`). |
| `openai` | Paid | Yes | Any OpenAI-compatible endpoint (`ai_base_url` for gateways such as OpenRouter or LM Studio). |
| `anthropic` | Paid | Yes | Claude via the official SDK. Default model `claude-opus-5-5`. |
| `none` | | | AI features hidden. |

### Using your existing Gemini integration (recommended)

1. In Home Assistant: Settings → Devices & services → *Google Generative AI* is already set up on your system (`ai_task.google_ai_task`).
2. In the App configuration, leave `ai_provider: home_assistant` and optionally set `ai_task_entity: ai_task.google_ai_task`.
3. Restart the App. **Settings** in the App shows "Using Home Assistant AI Task".

The App uses Home Assistant's API through the Supervisor (`homeassistant_api: true`), so no token or key is stored in the App.

## Limits and failure handling

- Images are downscaled to 1568 px on the long edge before sending (`ai_max_image_px`).
- Timeout: 60 s (`ai_timeout`); reading a manual allows at least 180 s. Retries: 2, with exponential back-off for rate limits, timeouts and server errors. Invalid JSON is retried.
- Confidence (0–1) is shown as high, medium or low.
- On failure, the photo is kept. Item entry continues manually, and container scans can be retried later. **AI failure never loses data.**
- Short keyword searches (fewer than three words) skip AI and are answered instantly.

## Privacy

Photos go to the provider you choose. Reading a document sends its first pages' text (about 12,000 characters) or its photo, with the item's name, make and model. Reading a manual sends the manual's text (for long manuals, only the pages about maintenance, up to about 90,000 characters) together with the equipment's make and model; never serial numbers or owner details. Google's free Gemini tier allows Google to use submitted content to improve its services. For full privacy, use `ollama` with a local model.
