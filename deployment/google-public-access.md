# Public Google sign-in

Public pages are served without sign-in at `/`, `/privacy/` and `/terms/`.
The sign-in page discloses AI processing and links to the policy and support contact.
The policy describes the deployed Render/Neon/OpenRouter configuration, not a
claim that the application has passed Google's verification.

## Operator checklist

1. Review the public policy and terms, including the support address and manual
   deletion-request process. Monitor that mailbox. Logout does not delete data.
2. Keep `LOG_PRIVATE_CONTENT=false` in production. In OpenRouter account privacy
   settings, do not opt in to prompt sharing/training. The client always requests
   `provider.data_collection=deny`, including outside free mode. Do not remove
   that filter if a free endpoint becomes unavailable.
3. In Google Auth Platform → Branding, set the deployed home, privacy and terms
   URLs and an accurate support contact. Avoid an optional logo until needed.
4. Verify ownership of the public URL with Google Search Console. Use the URL
   prefix method if DNS for the hosting provider's domain is not yours. Google
   decides whether the hosted domain is eligible; do not claim ownership of
   `onrender.com` or authorize unrelated domains.
5. In Audience, use External and Production. This expands who can request access;
   it does not grant every user's calendar access without their consent. Until
   verified, sensitive-scope sign-in can show a warning and has Google's lifetime
   100-user cap. Workspace administrators may independently block the app.
6. In Data Access, accurately register the scopes actually requested in
   `app/multiuser.py`. Justify Calendar access for search/create/update/delete,
   conflict detection and requested rescheduling. Do not request Gmail access.
   Web login requests `openid`, `userinfo.email`, `userinfo.profile` and
   `calendar.events`. The desktop CLI additionally requests
   `calendar.calendars.readonly` only to show the calendar title. It does not
   request the broad `calendar` scope. Existing tokens may retain earlier
   grants; changing the requested scopes does not retroactively revoke them.
7. Submit Google's verification form after completing ownership proof and a demo
   showing consent, the real OAuth client and calendar functionality. Use
   synthetic events, not private calendar/chat content in the public demo.
   Review any certifications/agreements before submitting. Approval is Google's
   decision and may require further responses from the project owner.
8. Test another account through the public callback after publishing; verify
   that its conversations and calendar cannot be read by a different account.

Do not delete existing local-development OAuth clients without the owner's
approval. Keep production secrets outside Git. Rotate previously exposed keys
before broadly sharing the app.

## Data deletion requests

Verify the requester's account ownership before any deletion. Do not treat an
email claiming another user's address as sufficient authorization. Stored data
must be removed across the user, session, conversation, preferences, assignments,
study-session and applicable reminder/usage records by a trusted operator.
Document provider backup retention separately. This is an operator workflow,
not an existing self-service account-delete feature. Never delete Google Calendar
events as a side effect of deleting Task Pilot records.

References: [Google sensitive-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification),
[Google OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies),
[OpenRouter provider privacy](https://openrouter.ai/docs/guides/privacy/data-collection).
