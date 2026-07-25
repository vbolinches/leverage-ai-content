# Monitoring and engagement

## What runs automatically

`monitor.py` (daily, 08:00 UTC via `.github/workflows/monitor.yml`) reports:

- follower and post counts
- per-post likes and comments for the last 12 posts
- best performer, weighted `likes + 3 x comments`
- comments with no reply from the account yet

It publishes nothing, replies to nothing, likes nothing. It asserts the token
resolves to `@leverageai.daily` and fails loudly otherwise — the same guard the
publisher uses, for the same reason (see README, wrong-account hazard).

## What is deliberately NOT automated

### Replying to comments and DMs

The monitor surfaces comments needing a reply; it does not answer them.

Auto-replying means an unreviewed model output is sent to a real person, in the
brand's voice, with no one reading it first. A wrong answer to a customer
question is materially worse than a weak post — the post can be deleted, the
reply has already been read. Volume is also tiny right now; surfacing them costs
nothing and answering takes a minute.

DMs additionally need webhook delivery and a published app (`instagram_business_manage_messages`
is granted, but development-mode apps receive no webhooks), so this is not
available today regardless.

**If you want drafted replies:** the sane version is a job that drafts a
suggested reply per pending comment and posts it into the Actions log for you to
copy or ignore. That keeps a human in the loop and is straightforward to add.

### Liking and following other accounts

Not automated, and not recommended.

Instagram's terms prohibit artificially collecting likes and followers and
prohibit automated interaction. Follow/like automation is among the most
reliably detected behaviours, and the penalty is an action block or ban on an
account that the entire pipeline depends on. The upside is marginal; the
downside removes the whole channel.

Five follows were placed by hand during setup (@openai, @notionhq, @canva,
@perplexity, @claudeai). Growing the follow graph further is best done manually,
a few at a time.

## Metrics not yet available

Reach, impressions, saves, and profile views need `instagram_business_manage_insights`,
which is not currently added to the app. Add it under
**App dashboard -> Use cases -> Instagram API -> Permissions and features**,
then extend `collect()` in `monitor.py` with a `/me/insights` call.

Follower counts and per-post likes/comments work today without it.
