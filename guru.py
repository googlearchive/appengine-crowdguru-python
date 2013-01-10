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

import datetime
import logging
import os
import re
import wsgiref.handlers
from google.appengine.api import xmpp
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.ereporter import report_generator
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp import xmpp_handlers


PONDER_MSG = "Hmm. Let me think on that a bit."
TELLME_MSG = "While I'm thinking, perhaps you can answer me this: %s"
SOMEONE_ANSWERED_MSG = ("We seek those who are wise and fast. One out of two "
                        "is not enough. Another has answered my question.")
ANSWER_INTRO_MSG = "You asked me: %s"
ANSWER_MSG = "I have thought long and hard, and concluded: %s"
WAIT_MSG = ("Please! One question at a time! You can ask me another once you "
            "have an answer to your current question.")
THANKS_MSG = "Thank you for your wisdom."
TELLME_THANKS_MSG = ("Thank you for your wisdom."
                     " I'm still thinking about your question.")
EMPTYQ_MSG = "Sorry, I don't have anything to ask you at the moment."
HELP_MSG = ("I am the amazing Crowd Guru. Ask me a question by typing '/tellme "
            "the meaning of life', and I will answer you forthwith! To learn "
            "more, go to %s/")
MAX_ANSWER_TIME = 120


class Question(db.Model):
  question = db.TextProperty(required=True)
  asker = db.IMProperty(required=True)
  asked = db.DateTimeProperty(required=True, auto_now_add=True)

  assignees = db.ListProperty(db.IM)
  last_assigned = db.DateTimeProperty()

  answer = db.TextProperty()
  answerer = db.IMProperty()
  answered = db.DateTimeProperty()

  @staticmethod
  def _tryAssignTx(key, user, expiry):
    """Assigns and returns the question if it's not assigned already.

    Args:
      key: db.Key: The key of a Question to try and assign.
      user: db.IM: The user to assign the question to.
    Returns:
      The Question object. If it was already assigned, no change is made
    """
    question = Question.get(key)
    if not question.last_assigned or question.last_assigned < expiry:
      question.assignees.append(user)
      question.last_assigned = datetime.datetime.now()
      question.put()
    return question

  @staticmethod
  def assignQuestion(user):
    """Gets an unanswered question and assigns it to a user to answer.

    Args:
      user: db.IM: The identity of the user to assign a question to.
    Returns:
      The Question entity assigned to the user, or None if there are no
        unanswered questions.
    """
    question = None
    while question == None or user not in question.assignees:
      # Assignments made before this timestamp have expired.
      expiry = (datetime.datetime.now()
                - datetime.timedelta(seconds=MAX_ANSWER_TIME))

      # Find a candidate question
      q = Question.all()
      q.filter("answerer =", None)
      q.filter("last_assigned <", expiry).order("last_assigned")
      # If a question has never been assigned, order by when it was asked
      q.order("asked")
      candidates = [x for x in q.fetch(2) if x.asker != user]
      if not candidates:
        # No valid questions in queue.
        break

      # Try and assign it
      question = db.run_in_transaction(Question._tryAssignTx,
                                       candidates[0].key(), user, expiry)

    # Expire the assignment after a couple of minutes
    return question

  def _unassignTx(self, user):
    question = Question.get(self.key())
    if user in question.assignees:
      question.assignees.remove(user)
      question.put()

  def unassign(self, user):
    """Unassigns the given user to this question.

    Args:
      user: db.IM: The user who will no longer be answering this question.
    """
    db.run_in_transaction(self._unassignTx, user)


class XmppHandler(xmpp_handlers.CommandHandler):
  """Handler class for all XMPP activity."""

  def _GetAsked(self, user):
    """Returns the user's outstanding asked question, if any."""
    q = Question.all()
    q.filter("asker =", user)
    q.filter("answer =", None)
    return q.get()

  def _GetAnswering(self, user):
    """Returns the question the user is answering, if any."""
    q = Question.all()
    q.filter("assignees =", user)
    q.filter("answer =", None)
    return q.get()

  def unhandled_command(self, message=None):
    # Show help text
    message.reply(HELP_MSG % (self.request.host_url,))

  def askme_command(self, message=None):
    im_from = db.IM("xmpp", message.sender)
    currently_answering = self._GetAnswering(im_from)
    question = Question.assignQuestion(im_from)
    if question:
      message.reply(TELLME_MSG % (question.question,))
    else:
      message.reply(EMPTYQ_MSG)
    # Don't unassign their current question until we've picked a new one.
    if currently_answering:
      currently_answering.unassign(im_from)

  def text_message(self, message=None):
    im_from = db.IM("xmpp", message.sender)
    question = self._GetAnswering(im_from)
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
                        ANSWER_INTRO_MSG % (question.question,))
      xmpp.send_message([question.asker.address], ANSWER_MSG % (message.arg,))

      # Send acknowledgement to the answerer
      asked_question = self._GetAsked(im_from)
      if asked_question:
        message.reply(TELLME_THANKS_MSG)
      else:
        message.reply(THANKS_MSG)

      # Tell any other assignees their help is no longer required
      if other_assignees:
        xmpp.send_message([x.address for x in other_assignees],
                          SOMEONE_ANSWERED_MSG)
    else:
      self.unhandled_command(message)

  def tellme_command(self, message=None):
    im_from = db.IM("xmpp", message.sender)
    asked_question = self._GetAsked(im_from)
    currently_answering = self._GetAnswering(im_from)

    if asked_question:
      # Already have a question
      message.reply(WAIT_MSG)
    else:
      # Asking a question
      asked_question = Question(question=message.arg, asker=im_from)
      asked_question.put()

      if not currently_answering:
        # Try and find one for them to answer
        question = Question.assignQuestion(im_from)
        if question:
          message.reply(TELLME_MSG % (question.question,))
          return
      message.reply(PONDER_MSG)


class LatestHandler(webapp.RequestHandler):
  """Displays the most recently answered questions."""

  def Render(self, template_file, template_values):
    path = os.path.join(os.path.dirname(__file__), 'templates', template_file)
    self.response.out.write(template.render(path, template_values))

  def get(self):
    q = Question.all().order('-answered').filter('answered >', None)
    template_values = {
      'questions': q.fetch(20),
    }
    self.Render("latest.html", template_values)


def main():
  app = webapp.WSGIApplication([
      ('/', LatestHandler),
      ('/_ah/xmpp/message/chat/', XmppHandler),
      ], debug=True)
  wsgiref.handlers.CGIHandler().run(app)


if __name__ == '__main__':
  main()
