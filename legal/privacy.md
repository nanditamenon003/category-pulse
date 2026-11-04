*Last updated 4 November 2026*

Category Pulse is a prototype built by one person as a personal project. It isn't a company product, and it hasn't been used by a real store.

**Only upload data you're allowed to use.** If the figures belong to your employer, or are confidential, get permission first. The sample store is there so you can try everything without uploading anything.

### What the app holds

- **Your sign-in details.** Auth0 handles sign-in, including "Continue with Google". The app receives your name and email address, and never sees your password.
- **The store data you upload.** While you're using the app, your file is read in memory, including columns it doesn't need. Only the columns in use are saved, so customer names and phone numbers in a report are dropped before saving.
- **Your settings.** Your store name, festivals, pairings and how your columns were read are saved along with your data.
- **A count of chat questions.** This enforces the daily limit. It's kept in the running app's memory, not in the database.

### How saved data is protected

Your data is encrypted by the app before it's saved to the database (Supabase), and stored against a one-way fingerprint of your login instead of your email address. The database provider sees only scrambled text.

The app itself holds the encryption key, because it has to open your data to show it to you. So this protects your data if the database is exposed, but the person running the app could technically open it. They don't look at it, but you should know that it's possible.

### Who else is involved

| Service | What it does | What it receives |
|---|---|---|
| Auth0 | Sign-in | Your name, email address and password |
| Streamlit Community Cloud | Runs the app | Whatever you upload while using it, and technical logs |
| Supabase | Saves your data | Encrypted data only |
| DeepSeek | Answers chat questions | Your question and the figures the chat looks up to answer it |

**The AI chat.** On your own data, the chat asks for a clear yes before it starts. After that, your questions and the figures it looks up are sent to DeepSeek, an outside company, under its own terms. Don't use the chat on confidential figures. Everything else in the app works without it.

These services may store data outside India.

Your data isn't sold, and it's not shared with anyone beyond the services listed above.

### Keeping and deleting your data

Saved data stays until you replace or delete it. **Delete my data**, on the Your data page, removes it from the database completely. Data you've already sent through the chat can't be recalled from DeepSeek.

To have your sign-in account removed, ask through the [project's GitHub page](https://github.com/nanditamenon003/category-pulse/issues). That page is public, so please don't post any figures or personal details there.

### Cookies

The app uses a cookie to keep you signed in. There's no advertising or tracking added by the app.
