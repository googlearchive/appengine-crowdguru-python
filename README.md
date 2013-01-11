# The Amazing Crowd Guru

"The Amazing Crowd Guru" is an example application that covers all the basic
functionality of Google App Engine's XMPP API.

The Amazing Crowd Guru is a veritable oracle, who can answer any question you
might pose it over XMPP. Thanks to a little behind-the-scenes trickery, we're
going to get our users to do all the work of answering questions for us.

The basic sequence of events will go like this:

- A user adds `crowdguru@appspot.com` to their buddy list in Google Talk, or
  another XMPP client.
- The user asks the Amazing Crowd Guru a question, by typing
  "/tellme Does a duck's quack echo?"
- Our code receives the question, stores it in the datastore as an unanswered
  question, then looks in the datastore for another unanswered question. If it
  finds one, it sends it back to the user, saying "While I'm thinking, perhaps
  you can answer me this: If a mole can dig a mole of holes, how many moles of
  holes can a mole of moles dig?"
- The user thinks a bit, and replies "A mole of moles can dig a mole of holes!"
- Our code receives the user's answer, stores it in the datastore alongside the
  original question, and then sends it back to the user who originally asked
  that question.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Python XMPP API][3]
- [NDB Datastore API][4]

## Dependencies
- [webapp2][5]
- [jinja2][6]


[1]: https://developers.google.com/appengine
[2]: https://python.org
[3]: https://developers.google.com/appengine/docs/python/xmpp/overview
[4]: https://developers.google.com/appengine/docs/python/ndb/
[5]: http://webapp-improved.appspot.com/
[6]: http://jinja.pocoo.org/docs/
