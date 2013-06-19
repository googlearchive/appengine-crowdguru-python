# Copyright 2009 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Crowdguru sample application using the XMPP service on Google App Engine."""



import datetime

from google.appengine.api import datastore_types
from google.appengine.api import xmpp
from google.appengine.ext import ndb
from google.appengine.ext.webapp import xmpp_handlers
import webapp2
from webapp2_extras import jinja2



PONDER_MSG = 'Hmm. Let me think on that a bit.'
TELLME_MSG = 'While I\'m thinking, perhaps you can answer me this: {}'
SOMEONE_ANSWERED_MSG = ('We seek those who are wise and fast. One out of two '
                        'is not enough. Another has answered my question.')
ANSWER_INTRO_MSG = 'You asked me: {}'
ANSWER_MSG = 'I have thought long and hard, and concluded: {}'
WAIT_MSG = ('Please! One question at a time! You can ask me another once you '
            'have an answer to your current question.')
THANKS_MSG = 'Thank you for your wisdom.'
TELLME_THANKS_MSG = THANKS_MSG + ' I\'m still thinking about your question.'
EMPTYQ_MSG = 'Sorry, I don\'t have anything to ask you at the moment.'
HELP_MSG = ('I am the amazing Crowd Guru. Ask me a question by typing '
            '\'/tellme the meaning of life\', and I will answer you forthwith! '
            'To learn more, go to {}/')
MAX_ANSWER_TIME = 120


class IMProperty(ndb.StringProperty):
    """A custom property for handling IM objects.

    IM or Instant Message objects include both an address and its protocol. The
    constructor and __str__ method on these objects allow easy translation from
    type string to type datastore_types.IM.
    """

    def _validate(self, value):
        """Validator to make sure value is an instance of datastore_types.IM.

        Args:
            value: The value to be validated. Should be an instance of
                datastore_types.IM.

        Raises:
            TypeError: If value is not an instance of datastore_types.IM.
        """
        if not isinstance(value, datastore_types.IM):
            raise TypeError('expected an IM, got {!r}'.format(value))

    def _to_base_type(self, value):
        """Converts native type (datastore_types.IM) to datastore type (string).

        Args:
            value: The value to be converted. Should be an instance of
                datastore_types.IM.

        Returns:
            String corresponding to the IM value.
        """
        return str(value)

    def _from_base_type(self, value):
        """Converts datastore type (string) to native type (datastore_types.IM).

        Args:
            value: The value to be converted. Should be a string.

        Returns:
            String corresponding to the IM value.
        """
        return datastore_types.IM(value)



class Question(ndb.Model):
    """Model to hold questions that the Guru can answer."""
    question = ndb.TextProperty(required=True)
    asker = IMProperty(required=True)
    asked = ndb.DateTimeProperty(required=True, auto_now_add=True)
    suspended = ndb.BooleanProperty(required=True)

    assignees = IMProperty(repeated=True)
    last_assigned = ndb.DateTimeProperty()

    answer = ndb.TextProperty(indexed=True)
    answerer = IMProperty()
    answered = ndb.DateTimeProperty()


    @staticmethod
    @ndb.transactional
    def _try_assign(key, user, expiry):
        """Assigns and returns the question if it's not assigned already.

        Args:
            key: ndb.Key: The key of a Question to try and assign.
            user: datastore_types.IM: The user to assign the question to.
            expiry: datetime.datetime: The expiry date of the question.

        Returns:
            The Question object. If it was already assigned, no change is made.
        """
        question = key.get()
        if not question.last_assigned or question.last_assigned < expiry:
            question.assignees.append(user)
            question.last_assigned = datetime.datetime.now()
            question.put()
        return question

    @classmethod
    def assign_question(cls, user):
        """Gets an unanswered question and assigns it to a user to answer.

        Args:
            user: datastore_types.IM: The identity of the user to assign a
                question to.

        Returns:
            The Question entity assigned to the user, or None if there are no
                unanswered questions.
        """
        question = None
        while question is None or user not in question.assignees:
            # Assignments made before this timestamp have expired.
            expiry = (datetime.datetime.now()
                      - datetime.timedelta(seconds=MAX_ANSWER_TIME))

            # Find a candidate question
            query = cls.query(cls.answerer == None, cls.last_assigned < expiry)
            # If a question has never been assigned, order by when it was asked
            query = query.order(cls.last_assigned, cls.asked)
            candidates = [candidate for candidate in query.fetch(2)
                          if candidate.asker != user]
            if not candidates:
                # No valid questions in queue.
                break

            # Try and assign it
            question = cls._try_assign(candidates[0].key, user, expiry)

        # Expire the assignment after a couple of minutes
        return question

    @ndb.transactional
    def unassign(self, user):
        """Unassigns the given user from this question.

        Args:
            user: datastore_types.IM: The user who will no longer be answering
                this question.
        """
        question = self.key.get()
        if user in question.assignees:
            question.assignees.remove(user)
            question.put()

    @classmethod
    def get_asked(cls, user):
        """Returns the user's outstanding asked question, if any.

        Args:
            user: datastore_types.IM: The identity of the user asking.

        Returns:
            An unanswered Question entity asked by the user, or None if there
                are no unanswered questions.
        """
        query = cls.query(cls.asker == user, cls.answer == None)
        return query.get()

    @classmethod
    def get_answering(cls, user):
        """Returns the question the user is answering, if any.

        Args:
            user: datastore_types.IM: The identity of the user answering.

        Returns:
            An unanswered Question entity assigned to the user, or None if there
                are no unanswered questions.
        """
        query = cls.query(cls.assignees == user, cls.answer == None)
        return query.get()



def bare_jid(sender):

    """Identify the user by bare jid.

    See http://wiki.xmpp.org/web/Jabber_Resources for more details.

    Args:
        sender: String; A jabber or XMPP sender.

    Returns:
        The bare Jabber ID of the sender.
    """

    return sender.split('/')[0]


class XmppHandler(xmpp_handlers.CommandHandler):
    """Handler class for all XMPP activity."""


    def unhandled_command(self, message=None):
        """Shows help text for commands which have no handler.

        Args:
            message: xmpp.Message: The message that was sent by the user.
        """
        message.reply(HELP_MSG.format(self.request.host_url))


    def askme_command(self, message=None):
        """Responds to the /askme command.

        Args:
            message: xmpp.Message: The message that was sent by the user.
        """
        im_from = datastore_types.IM('xmpp', bare_jid(message.sender))
        currently_answering = Question.get_answering(im_from)
        question = Question.assign_question(im_from)
        if question:
            message.reply(TELLME_MSG.format(question.question))
        else:
            message.reply(EMPTYQ_MSG)
        # Don't unassign their current question until we've picked a new one.
        if currently_answering:
            currently_answering.unassign(im_from)



    def text_message(self, message=None):
        """Called when a message not prefixed by a /cmd is sent to the XMPP bot.

        Args:
            message: xmpp.Message: The message that was sent by the user.
        """
        im_from = datastore_types.IM('xmpp', bare_jid(message.sender))
        question = Question.get_answering(im_from)
        if question:
            other_assignees = question.assignees
            other_assignees.remove(im_from)

            # Answering a question
            question.answer = message.arg
            question.answerer = im_from
            question.assignees = []
            question.answered = datetime.datetime.now()
            question.put()

            # Send the answer to the asker
            xmpp.send_message([question.asker.address],
                              ANSWER_INTRO_MSG.format(question.question))
            xmpp.send_message([question.asker.address],
                              ANSWER_MSG.format(message.arg))

            # Send acknowledgement to the answerer
            asked_question = Question.get_asked(im_from)
            if asked_question:
                message.reply(TELLME_THANKS_MSG)
            else:
                message.reply(THANKS_MSG)

            # Tell any other assignees their help is no longer required
            if other_assignees:
                xmpp.send_message([user.address for user in other_assignees],
                                  SOMEONE_ANSWERED_MSG)
        else:
            self.unhandled_command(message)



    def tellme_command(self, message=None):
        """Handles /tellme requests, asking the Guru a question.

        Args:
            message: xmpp.Message: The message that was sent by the user.
        """
        im_from = datastore_types.IM('xmpp', bare_jid(message.sender))
        asked_question = Question.get_asked(im_from)

        if asked_question:
            # Already have a question
            message.reply(WAIT_MSG)
        else:
            # Asking a question
            asked_question = Question(question=message.arg, asker=im_from)
            asked_question.put()

            currently_answering = Question.get_answering(im_from)
            if not currently_answering:
                # Try and find one for them to answer
                question = Question.assign_question(im_from)
                if question:
                    message.reply(TELLME_MSG.format(question.question))
                    return
            message.reply(PONDER_MSG)




class XmppPresenceHandler(webapp2.RequestHandler):
    """Handler class for XMPP status updates."""

    def post(self, status):
        """POST handler for XMPP presence.

        Args:
            status: A string which will be either available or unavailable
               and will indicate the status of the user.
        """
        sender = self.request.get('from')
        im_from = datastore_types.IM('xmpp', bare_jid(sender))
        suspend = (status == 'unavailable')
        query = Question.query(Question.asker == im_from,
                               Question.answer == None,
                               Question.suspended == (not suspend))
        question = query.get()
        if question:
            question.suspended = suspend
            question.put()



class LatestHandler(webapp2.RequestHandler):
    """Displays the most recently answered questions."""

    @webapp2.cached_property
    def jinja2(self):
        """Cached property holding a Jinja2 instance.

        Returns:
            A Jinja2 object for the current app.
        """
        return jinja2.get_jinja2(app=self.app)

    def render_response(self, template, **context):
        """Use Jinja2 instance to render template and write to output.

        Args:
            template: filename (relative to $PROJECT/templates) that we are
                rendering.
            context: keyword arguments corresponding to variables in template.
        """
        rendered_value = self.jinja2.render_template(template, **context)
        self.response.write(rendered_value)

    def get(self):
        """Handler for latest questions page."""
        query = Question.query(Question.answered > None).order(
                -Question.answered)
        self.render_response('latest.html', questions=query.fetch(20))



APPLICATION = webapp2.WSGIApplication([

        ('/', LatestHandler),

        ('/_ah/xmpp/message/chat/', XmppHandler),
        ('/_ah/xmpp/presence/(available|unavailable)/', XmppPresenceHandler),
        ], debug=True)

