#!/usr/bin/env python
from datetime import datetime
import json
import os
import time
import logging
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import urlfetch
from google.appengine.ext import ndb
from google.appengine.api import memcache
from google.appengine.api import taskqueue

from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import ProfileForms
from models import TeeShirtSize
from models import StringMessage
from models import BooleanMessage
from models import ConflictException
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms

from settings import WEB_CLIENT_ID
from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SPEAKER_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker_name=messages.StringField(1),
    speaker_bio=messages.StringField(2),
    speaker_email=messages.StringField(3)
)

SESSION_CREATE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    speakerKey=messages.StringField(2),
    session_name=messages.StringField(3),
    startDate=messages.StringField(4),
    startTime=messages.StringField(5),
    duration=messages.StringField(6),
    typeOfSession=messages.StringField(7),
)

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speakerKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionKey=messages.StringField(1),
)

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
    'EQ': '=',
    'GT': '>',
    'GTEQ': '>=',
    'LT': '<',
    'LTEQ': '<=',
    'NE': '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api(name='conference',
               version='v1',
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

    # - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    @staticmethod
    def _createConferenceObject(self, request):
        """Create or update Conference object, returning
        ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException(
                "Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in
                request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects;
        # set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10],
                                                "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # send confirmation email
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(
                filtr["field"],
                filtr["operator"],
                filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name)
                     for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid \
                    field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                """check if inequality operation has been used in previous
                filters disallow the filter if inequality was performed on a
                different field before track the field on which the
                inequality operation is performed
                """
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is \
                        allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                "No conference found with key: %s" % request.websafeConferenceKey)  # noqa
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST',
                      name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Query for conferences."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for all key matches for this user
        conferences = Conference.query(ancestor=p_key)
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName)
                   for conf in conferences]
        )

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId))
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(
                    conf,
                    names[conf.organizerUserId])
                   for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, ProfileForms,
                      path='getConferenceAttendees',
                      http_method='POST',
                      name='getConferenceAttendees')
    def getConferenceAttendees(self, request):
        """Allows the creator of the conference to list all attendees"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        wsck = request.websafeConferenceKey
        confKey = ndb.Key(urlsafe=wsck)
        conf = confKey.get()
        # check that conference exists
        if not conf:
            raise endpoints.BadRequestException(
                'No conference found with key: %s' % wsck)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner retrieve the attendees.')

        q = Profile.query()
        attendees = q.filter(Profile.conferenceKeysToAttend == wsck)
        return ProfileForms(
            items=[self._copyProfileMiniToForm(prof)
                   for prof in attendees
                   ]
        )

    # - - - Speakers - - - - - - - - - - - - - - - - - - - -

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf

    @endpoints.method(SPEAKER_POST_REQUEST, BooleanMessage,
                      path='speakers/add',
                      http_method='POST',
                      name='addSpeaker')
    def addSpeaker(self, request):
        """Registers a new speaker"""
        prof = self._getProfileFromUser()
        data = {field.name: getattr(request, field.name) for field in
                request.all_fields()}
        s_id = Speaker.allocate_ids(size=1)[0]
        speaker_key = ndb.Key(Speaker, s_id)
        data['key'] = speaker_key
        Speaker(**data).put()
        return BooleanMessage(data=True)

    @endpoints.method(message_types.VoidMessage, SpeakerForms,
                      path='speakers/get',
                      http_method='POST',
                      name='getSpeakers')
    def getSpeakers(self, request):
        """Returns a list of speakers"""
        speakers = Speaker.query()
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker)
                   for speaker in speakers
                   ]
        )

    @endpoints.method(CONF_GET_REQUEST, SpeakerForms,
                      path='speakers/getPresenters/{websafeConferenceKey}',
                      http_method='POST',
                      name='getPresenters')
    def getSpeakersByConference(self, request):
        """Returns a list of speakers presenting at a conference"""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=confKey, projection=["speakerKey"],
                                 distinct=True)
        speakerKeys = [(ndb.Key(urlsafe=sess.speakerKey))
                       for sess in sessions]
        presenters = ndb.get_multi(speakerKeys)
        return SpeakerForms(
            items=[self._copySpeakerToForm(presenter)
                   for presenter in presenters
                   ]
        )

    # - - - Registration - - - - - - - - - - - - - - - - - - - -
    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()
        conf_keys = [ndb.Key(urlsafe=wsck)
                     for wsck in prof.conferenceKeysToAttend]

        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in
                      conferences]
        profiles = ndb.get_multi(organisers)
        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(
                conf,
                names[conf.organizerUserId])
                   for conf in conferences]
        )

    # - - - Session objects - - - - - - - - - - - - - - - - - - -
    def _copySessionToForm(self, sess, conferenceName, speakerName):
        """Copy relevant fields from Session to SessionForm."""
        session = SessionForm()
        for field in session.all_fields():
            if hasattr(sess, field.name):

                if field.name.endswith('Date'):
                    # convert Date to date string; just copy others
                    setattr(
                        session,
                        field.name,
                        str(getattr(sess, field.name)))
                elif field.name.endswith('Time'):
                    # convert Time to time string; just copy others
                    setattr(
                        session,
                        field.name,
                        str(getattr(sess, field.name)))
                else:
                    setattr(session, field.name, getattr(sess, field.name))
            elif field.name == "websafeKey":
                setattr(session, field.name, sess.key.urlsafe())
        if conferenceName:
            setattr(session, 'conferenceName', conferenceName)
        if speakerName:
            setattr(session, 'speakerName', speakerName)
        session.check_initialized()
        return session

    @endpoints.method(SESSION_CREATE, SessionForm, path='session',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Registers a new session"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = confKey.get()
        # check that conference exists
        if not conf:
            raise endpoints.BadRequestException(
                'No conference found with key: %s' % request.websafeConferenceKey)  # noqa

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can add sessions to a conference.')
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        del data['websafeConferenceKey']

        if data['startDate']:
            data['startDate'] = datetime.strptime(
                data['startDate'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(
                data['startTime'], "%H:%M").time()
        if data['duration']:
            data['duration'] = int(data['duration'])
        else:
            data['duration'] = 0

        s_id = Session.allocate_ids(size=1, parent=confKey)[0]
        s_key = ndb.Key(Session, s_id, parent=confKey)
        data['key'] = s_key
        session = Session(**data)
        session.put()

        # send confirmation email
        taskqueue.add(params={'speakerKey': request.speakerKey,
                              'conferenceKey': request.websafeConferenceKey},
                      url='/tasks/set_featured_speaker'
                      )

        return self._copySessionToForm(session, "", "")

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='getConferenceSessions/{websafeConferenceKey}',
                      http_method='POST',
                      name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a conference, returns all sessions."""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=confKey)
        conferenceName = confKey.get().name
        return SessionForms(
            items=[self._copySessionToForm(
                sess,
                conferenceName,
                ndb.Key(urlsafe=sess.speakerKey).get().speaker_name
            )
                   for sess in sessions]
        )

    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms,
                      path='getConferenceSessionsByType/{websafeConferenceKey}/{typeOfSession}',  # noqa
                      http_method='POST',
                      name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Given a conference, return all sessions of a specified type
        (eg lecture, keynote, workshop)"""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=confKey).filter(
            Session.typeOfSession == request.typeOfSession)
        conferenceName = confKey.get().name
        return SessionForms(
            items=[self._copySessionToForm(sess, conferenceName, ndb.Key(
                urlsafe=sess.speakerKey).get().speaker_name)
                   for sess in sessions]
        )

    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms,
                      path='getSessionsBySpeaker/{speakerKey}',
                      http_method='GET',
                      name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Given a speaker, return all sessions given by this
        particular speaker, across all conferences"""
        wssk = request.speakerKey

        sessions = Session.query().filter(Session.speakerKey == wssk)
        speaker = ndb.Key(urlsafe=wssk).get()
        return SessionForms(
            items=[self._copySessionToForm(
                sess,
                sess.key.parent().get().name,
                speaker.speaker_name)
                   for sess in sessions]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='getSessionByTypeAndTime',
                      http_method='GET',
                      name='getSessionByTypeAndTime')
    def getSessionByTypeAndTime(self, request):
        """Returns all sessions that are not workshops and are before 19:00"""
        sessions = Session.query(Session.typeOfSession != "workshop").fetch()
        validSessions = []
        for sess in sessions:
            if sess.startTime < datetime.strptime("19:00", "%H:%M").time():
                validSessions.append(sess)

        return SessionForms(
            items=[self._copySessionToForm(
                sess,
                sess.key.parent().get().name,
                ndb.Key(urlsafe=sess.speakerKey).get().speaker_name)
                   for sess in validSessions]
        )

    # - - - Wishlist objects - - - - - - - - - - - - - - - - - - -

    def _wishlistRegistration(self, request, add=True):
        """Adds or Removes a session from the users wishlist"""
        retval = False
        prof = self._getProfileFromUser()
        sessionKey = request.sessionKey
        session = ndb.Key(urlsafe=sessionKey).get()
        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % sessionKey)
        if add:
            # check if session is already in wishlist
            if sessionKey in prof.sessionWishlist:
                raise ConflictException(
                    "This session is already in your wishlist")
            # if not in wishlist add
            prof.sessionWishlist.append(sessionKey)
            retval = True
        else:
            # check if session is in wishlist
            if sessionKey in prof.sessionWishlist:
                # remove session
                prof.sessionWishlist.remove(sessionKey)
                retval = True
            else:
                # raise exception for the session not existing in the list
                raise ConflictException(
                    "This session does not exist in the wishlist")
        # save data back to datastore
        prof.put()
        return BooleanMessage(data=retval)

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
                      path='addSessionToWishlist/{sessionKey}',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to users wishlist."""
        return self._wishlistRegistration(request)

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
                      path='removeSessionFromWishlist/{sessionKey}',
                      http_method='POST', name='removeSessionFromWishlist')
    def removeSessionFromWishlist(self, request):
        """Remove session from users wishlist."""
        return self._wishlistRegistration(request, add=False)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='getSessionsInWishlist',
                      http_method='POST', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Gets sessions in users wishlist."""
        prof = self._getProfileFromUser()  # get user Profile
        sess_keys = [ndb.Key(urlsafe=sessionKey)
                     for sessionKey in prof.sessionWishlist]
        sessions = ndb.get_multi(sess_keys)
        return SessionForms(
            items=[self._copySessionToForm(
                sess,
                sess.key.parent().get().name,
                ndb.Key(urlsafe=sess.speakerKey).get().speaker_name)
                   for sess in sessions]
        )

    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name,
                            getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _copyProfileMiniToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileMiniForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name,
                            getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if
        non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()
        return profile  # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        prof.put()
        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    # TODO 1
    # 1. change request class
    # 2. pass request to _doProfile function
    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""

        return self._doProfile(request)

    # - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

    # - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheFeaturedSpeaker(speakerKey, conferenceKey):
        """Check if speaker has another session at the same conference
        """
        speakerKey = speakerKey
        confKey = ndb.Key(urlsafe=conferenceKey)
        query = Session.query(ancestor=confKey)
        sessionCount = query.filter(Session.speakerKey == speakerKey).count()
        featuredSpeaker = ""
        if sessionCount > 1:
            sKey = ndb.Key(urlsafe=speakerKey)
            speaker = sKey.get()
            featuredSpeaker = '%s %s' % (
                'The featured speaker for this conference is: ',
                speaker.speaker_name)
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, featuredSpeaker)
        return featuredSpeaker

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/speaker/get',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker from memcache."""
        featuredSpeaker = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
        if not featuredSpeaker:
            featuredSpeaker = ""
        return StringMessage(data=featuredSpeaker)


# registers API
api = endpoints.api_server([ConferenceApi])
