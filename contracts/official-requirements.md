# Official documents: implementation notes

Source: CQA Hackathon Student Guide v3 (4 pages), Chat Quality Checklist (3 pages), supplied locally.
PDFs, signed URLs and real source records remain outside Git.

Initial configuration is `backend/app/default_checklist.py`. Weights 1–7: 10,15,20,20,15,15,5.
Criterion 8 is a zero-weight gross-error flag. Criterion 4 steps: 0,5,10,15,20; criterion 6: 0,4,7,10,
weight 15. N/A is allowed for 2,3,4,7 only, under their documented conditions.
The exact descriptions, sections and conditions are in the configuration, exposed via GET /api/v1/checklists.

- Same checklist for RU/KZ/EN; evaluate the operator, bot/system messages are context.
- The three-minute first-response threshold, tags and attachments are excluded from scoring.
- Each criterion requires reasoning and evidence message IDs. For missing actions reference the place
  where the action was expected. For N/A explain the fulfilled condition.
- Generic tariff questions do not require identification. Required identity verification not confirmed
  in the conversation means false, not N/A.
- Consultation completeness and dialogue clarity are independent.
- Offering personal investment transfers or paid private consultation sets the gross-error flag even
  if the client did not agree or transfer money. Never automatically zero the total.
- UI evidence links must navigate to messages/call fragments. `metadata.audio_url` supports playback
  or a link to a player. Preserve original timestamps (MM:SS or ISO8601 with timezone).
- Keep the HTML/CSS/JavaScript UI separable from the service so it can later be adapted into a
  Dynamics 365 HTML component. React/Vite is the team's chosen implementation.
- Show original evaluation, reviewer decision/reason and final result. No sign-in is required for demo.
- An edited/new criterion must run without code changes; create a new checklist version.
- The paid-model handoff must document model name and approximate full evaluation cost across all calls.
  Neither a confidence threshold nor a particular export format/quality metric is specified in the PDFs.

Organizer API: server-side GET of local `DEV_API_URL`. Request `group_id` defaults to DEV;
only this query parameter is changed for the future control set. Success envelope:
`{error:"",isSuccess:true,data:{dataset_group_id,total_records,records:[...]}}`.
Each record has `conversation_id`, `channel`, `language`, `transcript` array of
`{timestamp,speaker,text}`, and `audio_url`. Preserve all values locally. Expected DEV size:65.
The API does not return reference quality scores, so agreement with organizers cannot yet be measured.

At +23h organizers provide a control set; at +24h code is frozen. Data processing and checklist
configuration remain allowed after freeze. Before submission a public repository and a link to
organizers are required. Development repository stays private per the user's instruction; publication
is a later release step. Presentation is at most five minutes.
