
# P4 Developing Scalable Apps
This project is utilising the Google App Engine and Datastore to develop a backend API for a conference management system. The main focus of this project is the API, which allows the creation of Conferences, Sessions and Speakers and allows authenticated users to register for a conference and add sessions to their wish list

##Requirements
- Python (version 2.7)
- To run the code through the localhost you will need  the Google App Engine Launcher
- Otherwise the APIs can be viewed at https://striking-center-104307.appspot.com/_ah/api/explorer
- Internet connection required for OAuth authentication and viewing the API

##Contents
-  app.yaml - contains the configuration and routes for the APIs
- cron.yaml - contains the configuration for the scheduled tasks (currently the setting of the announcements are run through a  scheduled task, which runs every 60 minutes)
- index.yaml - contains the indexes required by the datastore queries
- conference.py - API for the Conference Central application
- main.py - stores the functions related to the background tasks
- models.py - stores the data models and output forms
- settings.py - stores the Google App Engine project id
- utils.py - function for retrieving the user details
- static - contains HTML for the web interface for the Conference Central site
- templates - contains templates and scripts for the Conference Central site


## Instructions
### Viewing the Conference Central App - locally
1. Open Google App Engine Launcher
2. Go to File -> Add Existing Application
3. Select the folder containing the Application
4. Select the application in the list and click Run
5. Open your preferred web browser and navigate to http://localhost:8080/

### Using the Google API Explorer to view the APIs
1. Follow steps 1 - 4 above
2. Open your web browser and navigate to http://localhost:8080/_ah/api/explorer

### Deploying the App
1. Create a project through the Google Developer Console
2. Update the WEB_CLIENT_ID in settings.py with the provided client ID
3. Update the CLIENT_ID in the oauth2 settings in \static\js\app.js (line 93)
4. Open the Google App Engine Launcher
5. Click Deploy
6. Open your web browser and navigate to https://{{PROJECT_ID}}.appspot.com/ (replacing {{PROJECT_ID}}, with the ID for the project created in the Google Developer Console)

### Viewing the currently deployed version
The current deployed version can be viewed at https://striking-center-104307.appspot.com/ and the API's viewed at https://striking-center-104307.appspot.com/_ah/api/explorer



##Project Notes
###Viewing API for assignment
The API's for the assignment can be viewed at   https://striking-center-104307.appspot.com/_ah/api/explorer

###Task 1 - Add Sessions to conferences
For this task created 2 new tables Speakers (to store details about the speaker, such as name, email and bio) and Sessions (to store details about a session which is linked to a conference). The sessions table can be queried by the conference, speaker and type of session. A Session record is linked to Conference record and a Speaker is linked to the session through the session key. 

The Speakers table is made up of the following fields:

- speaker_name - string field which is mandatory for storing the speakers name
- speaker_email - string field for storing the speakers email address. The storing of the speaker's email address could allow the app to be extended further through a task or scheduled job to send the speaker a list of people attending their sessions
- speaker_bio - text field, which is used for storing a brief bio for a speaker. This could be used for the featured speaker function and also providing some details about a speaker to help attendees determine which sessions they would like to attend

The Sessions table is made up of the following fields:

- session_name - string field containing the name for the session
- highlights - string field for brief description of session
- duration - integer field, used for storing the number of minutes that a session runs for
- typeOfSession - string field, used for storing the type of session for example workshop, keynote, lecture
- startDate - date field, used for storing the date that the session starts
- startTime - time field, used for storing the time that the session starts
- speakerKey - string field, used for storing the key field of the speaker for the session


###Task 2 - Add Sessions to User Wish List
This task involved allowing the user to register / unregister their interest in each session. 

- conference.addSessionToWishlist - allows the currently logged in user to add a session to their wish list, which is stored in the user profile.
- conference.removeSessionFromWishlist - allows the currently logged in user to remove the session from their wish list
- conference.getSessionsInWishlist - returns the sessions in the logged in user's wish list


###Task 3 - Create additional queries
This task required the creation of 2 additional queries for the Conference Central application:

1. conference.getConferenceAttendees - this query allows the creator of a conference to return a list of registered attendees. Currently this query accepts the web safe conference key and then returns a list of the profiles for the attendees
2. conference.getSpeakers - this query returns a list of speakers and their bio's

The next part of this task required a query that could find sessions that weren't workshops and started before 7PM. The difficulty with this query is caused by a limitation of querying the datastore, currently each query can only contain one inequality condition (ie not equal to). In order to find sessions that are not workshops and are not after 7PM, two inequality conditions would be required. To resolve this problem, I first queried the sessions by the type of session, then I looped through the results and excluded results which occurred after 7PM

###Task 4 - Adding a task
This task involved adding a task that would run in the background after a new session is added. When a new session is added to a conference the task "SetFeaturedSpeaker" is run in the background, this task updates the Memcache with the speaker who has the most sessions.







