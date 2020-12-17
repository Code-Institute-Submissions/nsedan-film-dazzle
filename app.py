import os
import random
import requests
from datetime import datetime

import omdb
import bcrypt
import uuid

from flask import (Flask, render_template,
                   redirect, request, url_for, session, flash)
from flask_pymongo import PyMongo, pymongo

if os.path.exists("env.py"):
    import env


app = Flask(__name__)

app.config["MONGO_DBNAME"] = "film_dazzle"
app.config["MONGO_URI"] = os.environ.get('MONGO_URI')
app.config["SECRET_KEY"] = os.environ.get('SECRET_KEY')

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')

API_KEY = os.environ.get('API_KEY')
omdb.set_default('apikey', API_KEY)


mongo = PyMongo(app)


@app.route('/')
@app.route('/index')
def index():
    """Popular titles based on latest reviews"""
    reviews = mongo.db.reviews.find()
    sorted_reviews = reviews.sort('_id', pymongo.DESCENDING)

    # Avoid duplicates
    duplicates_list = []
    for review in sorted_reviews:
        imdb_id = review['imdb_id']
        duplicates_list.append(imdb_id)

    duplicates_dict = dict.fromkeys(duplicates_list)
    duplicates_keys = duplicates_dict.keys()

    no_duplicates = []
    for keys in duplicates_keys:
        no_duplicates.append(keys)

    # Pull titles from Mongo
    titles_list = []
    for ids in no_duplicates:
        titles = mongo.db.titles.find_one({'imdb_id': ids})
        titles_list.append(titles)

    """Top 10 box office success titles"""
    boxoffice = mongo.db.boxoffice.find().limit(10)
    titles = mongo.db.titles.find()
    boxoffice_limited = []

    for bo in boxoffice:
        imdb_id = bo['imdb_id']
        titles = mongo.db.titles.find_one({'imdb_id': imdb_id})
        poster = titles['poster']
        bo['poster'] = poster
        boxoffice_limited.append(bo)

    if 'username' in session:
        username = session['username']
        return render_template("index.html", titles=titles_list[0:10],
                               boxoffice=boxoffice_limited, username=username)

    return render_template("index.html", titles=titles_list[0:10],
                           boxoffice=boxoffice_limited)


@app.route('/login', methods=["GET", "POST"])
def login():
    '''Login checks if the user exists and if the
       user/password combination is correct'''
    if request.method == 'POST':
        get_username = request.form.get('username')
        get_password = request.form.get('password')

        users = mongo.db.users
        user_exists = users.find_one({'username': get_username})

        if user_exists:
            usr_pass = user_exists['password']
            if bcrypt.hashpw(get_password.encode('utf-8'),
                             usr_pass) == usr_pass:
                session['username'] = get_username
                return redirect(url_for('index'))

        flash('Invalid username/password combination')
    return render_template("login.html")


@app.route('/register', methods=["GET", "POST"])
def register():
    '''Register checks if the user exists and creates a new user on MongoDB'''
    get_username = request.form.get('username')
    get_password = request.form.get('password')

    if request.method == 'POST':
        users = mongo.db.users
        user_exists = mongo.db.users.find_one({'username': get_username})

        if not user_exists:
            date = datetime.now().strftime("%B, %Y")
            hashed_password = bcrypt.hashpw(get_password.encode('utf-8'),
                                            bcrypt.gensalt())
            users.insert_one({'username': get_username,
                              'password': hashed_password,
                              'user_id': str(uuid.uuid4()),
                              'created_date': date})
            session['username'] = get_username
            return redirect(url_for('index'))

        flash('That username already exists!')
    return render_template('register.html')


@app.route("/profile")
def profile():
    '''Profile is only available if there is a user in session'''
    if "username" in session:
        username = session['username']
        user_info = mongo.db.users.find_one({'username': username})
        reviews = mongo.db.reviews
        user_reviews = reviews.find({'user': username})
        sorted_reviews = user_reviews.sort('_id', pymongo.DESCENDING)

        return render_template("profile.html", username=username,
                               user_info=user_info,
                               user_reviews=sorted_reviews)
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    '''Logout is only available if there is a user in session'''
    session.pop('username', None)
    return redirect(url_for('index'))


@app.route('/search', methods=["GET", "POST"])
def search():
    '''Takes the input from a user search to the result route'''
    if request.method == "POST":
        user_search = request.form.get('query')
        return redirect(url_for('result', query=user_search.lower()))
    else:
        return redirect(url_for('index'))


@app.route('/search/<query>')
def result(query):
    '''Request the user search to OMDB API to render results'''
    data_request = omdb.search_movie(query, page=1)
    if data_request:
        return render_template("results.html",
                               data_request=data_request, query=query)
    else:
        return render_template("no_results.html")


@app.route('/add_title/<users_choice>')
def add_title(users_choice):
    '''When user clicks on a particular result this checks if the
       movies is already on MongoDB to avoid duplicates, reduce
       the data to remove unwanted information and add the movie trailer.'''
    data_request = omdb.imdbid(users_choice)
    data_reduced = data_request
    keys = {
        "dvd", "website", 'ratings', 'imdb_votes',
        "language", "response", "type", 'box_office'
    }
    for key in keys:
        del data_reduced[key]

    #  Request to Youtube API
    search_url = 'https://www.googleapis.com/youtube/v3/search'
    search_params = {
        'key': YOUTUBE_API_KEY,
        'q': f"{data_reduced['title']} official trailer",
        'part': 'snippet',
        'maxResults': 1,
        'type': 'video'
    }
    results = requests.get(search_url, params=search_params).json()
    items = results['items']
    for item in items:
        video_id = item['id']['videoId']
    data_reduced['youtube_id'] = video_id
    data_reduced['users_rating'] = 'N/A'

    # Push to Mongo
    titles = mongo.db.titles
    title_exists = titles.find_one({'imdb_id': users_choice})

    if not title_exists:
        titles.insert_one(data_reduced)
        find_imdb_id = titles.find_one({'imdb_id': users_choice})
        imdb_id = find_imdb_id['imdb_id']
    else:
        imdb_id = title_exists.get('imdb_id')
    return redirect(url_for('title', title_id=imdb_id))


@app.route('/title/<title_id>')
def title(title_id):
    '''Render a particular title with its reviews, checks if there is a user
       in session and if this user has already left a review for this title.'''
    title = mongo.db.titles.find_one({'imdb_id': title_id})
    id_exists = title['imdb_id']

    # Load Reviews
    imdb_id = title['imdb_id']
    reviews = mongo.db.reviews.find({'imdb_id': imdb_id})
    sorted_reviews = reviews.sort('_id', pymongo.DESCENDING)

    if not id_exists:
        return redirect(url_for("404"))
    else:
        if 'username' in session:
            username = session['username']
            user_review = list(mongo.db.reviews.find({'user': username,
                                                      'imdb_id': title_id}))
            return render_template('title.html', title=title,
                                   reviews=sorted_reviews,
                                   username=username,
                                   user_review=user_review)

        return render_template('title.html', title=title,
                               reviews=sorted_reviews)


@app.route('/title/<title_id>/review/create', methods=["GET", "POST"])
def create(title_id):
    '''Request to create a review for a movie a push it to MongoDB
       and update the title rating'''
    if request.method == "POST":
        title = mongo.db.titles.find_one({'imdb_id': title_id})
        imdb_id = title['imdb_id']
        movie_title = title['title']

        # Push user review to Mongo
        user_review = request.form.get('text')
        user_name = request.form.get('user')
        user_rating = request.form.get('rating')
        user_title = request.form.get('title')
        date = datetime.now().strftime("%B %d, %Y at %H:%M:%S")
        review = {'title': user_title,
                  'user': user_name,
                  'text': user_review,
                  'rating': user_rating,
                  'imdb_id': imdb_id,
                  'movie_title': movie_title,
                  'review_id': str(uuid.uuid4()),
                  'date': date}
        mongo.db.reviews.insert_one(review)

        # Ratings to Mongo
        reviews = list(mongo.db.reviews.find({'imdb_id': title_id}))
        reviews_length = len(reviews)
        reviews_sum = 0
        for review in reviews:
            review_int = int(review['rating'])
            reviews_sum = reviews_sum + review_int
        avg = round(reviews_sum / reviews_length, 1)
        mongo.db.titles.update_one({'imdb_id': title_id},
                                   {'$set': {'users_rating': str(avg)}})

        # Reload page
        imdb_id = title['imdb_id']
        return redirect(url_for('title', title_id=imdb_id))
    else:
        return redirect(url_for('index'))


@app.route('/review/<review_id>')
def show(review_id):
    '''Request to show a particular review. This checks if the user is the same
       that created the review to later allow edit and delete options.'''
    review = mongo.db.reviews.find_one({'review_id': review_id})
    review_id = review['review_id']
    imdb_id = review['imdb_id']
    title = mongo.db.titles.find_one({'imdb_id': imdb_id})
    if 'username' in session:
        username = session['username']
        return render_template('review.html', review_id=review_id,
                               review=review, title=title, username=username)

    return render_template('review.html', review_id=review_id,
                           review=review, title=title)


@app.route('/review/<title_id>/<review_id>')
def destroy(review_id, title_id):
    '''Request to delete a review, only if the user created it,
       and updates title rating'''
    review = mongo.db.reviews.delete_one({'review_id': review_id})

    # Update rating and push to Mongo
    reviews = list(mongo.db.reviews.find({'imdb_id': title_id}))
    reviews_length = len(reviews)
    reviews_sum = 0
    for review in reviews:
        review_int = int(review['rating'])
        reviews_sum = reviews_sum + review_int
    if reviews_sum == 0:
        avg = 'N/A'
    else:
        avg = round(reviews_sum / reviews_length, 1)
    mongo.db.titles.update_one({'imdb_id': title_id},
                               {'$set': {'users_rating': str(avg)}})

    return redirect(url_for('title', title_id=title_id))


@app.route('/review/<review_id>/edit')
def edit(review_id):
    '''Render review form to edit, passing the selected review values'''
    review = mongo.db.reviews.find_one({'review_id': review_id})
    return render_template('review_edit.html', review=review)


@app.route('/title/<title_id>/<review_id>', methods=["GET", "POST"])
def update(review_id, title_id):
    '''Updates users reviews and title ratings'''
    user_review = request.form.get('text')
    user_rating = request.form.get('rating')
    user_title = request.form.get('title')
    date = datetime.now().strftime("%B %d, %Y at %H:%M:%S")
    review = mongo.db.reviews
    review.update_one({'review_id': review_id},
                      {'$set': {'title': user_title,
                                'text': user_review,
                                'rating': user_rating,
                                'date': date}})

    # Update rating and push to Mongo
    reviews = list(mongo.db.reviews.find({'imdb_id': title_id}))
    reviews_length = len(reviews)
    reviews_sum = 0
    for review in reviews:
        review_int = int(review['rating'])
        reviews_sum = reviews_sum + review_int
    avg = round(reviews_sum / reviews_length, 1)
    mongo.db.titles.update_one({'imdb_id': title_id},
                               {'$set': {'users_rating': str(avg)}})

    return redirect(url_for('title', title_id=title_id))


@app.route('/top_imdb')
def top_imdb():
    '''Sort titles by IMDb rating a renders the first 10.'''
    titles = mongo.db.titles.find()
    sorted_titles = titles.sort('imdb_rating', pymongo.DESCENDING)
    sorted_titles_list = []
    for sorted_title in sorted_titles:
        if not sorted_title['imdb_rating'] == 'N/A':
            sorted_titles_list.append(sorted_title)
    if 'username' in session:
        username = session['username']
        return render_template('top_imdb.html',
                               titles=sorted_titles_list[0:10],
                               username=username)

    return render_template('top_imdb.html', titles=sorted_titles_list[0:10])


@app.route('/top_metacritic')
def top_metacritic():
    '''Sort titles by Metacritic rating a renders the first 10.'''
    titles = mongo.db.titles.find()
    sorted_titles = titles.sort('metascore', pymongo.DESCENDING)
    sorted_titles_list = []
    for sorted_title in sorted_titles:
        if not sorted_title['metascore'] == 'N/A':
            sorted_titles_list.append(sorted_title)
    if 'username' in session:
        username = session['username']
        return render_template('top_metacritic.html',
                               titles=sorted_titles_list[0:10],
                               username=username)

    return render_template('top_metacritic.html',
                           titles=sorted_titles_list[0:10])


@app.route('/top_users')
def top_users():
    '''Sort titles by users rating a renders the first 10.'''
    titles = mongo.db.titles.find()
    sorted_titles = titles.sort('users_rating', pymongo.DESCENDING)
    sorted_titles_list = []
    for sorted_title in sorted_titles:
        if not sorted_title['users_rating'] == 'N/A':
            sorted_titles_list.append(sorted_title)
    if 'username' in session:
        username = session['username']
        return render_template('top_users.html',
                               titles=sorted_titles_list[0:10],
                               username=username)

    return render_template('top_users.html', titles=sorted_titles_list[0:10])


@app.route('/box_office')
def box_office():
    '''Render Box Office data from MongoDB and allows pagination'''
    boxoffice = mongo.db.boxoffice.find()
    offset = request.args.get('offset', 0, type=int)
    limit = 25
    boxoffice_limited = boxoffice.skip(offset).limit(limit)
    if 'username' in session:
        username = session['username']
        return render_template('box_office.html', boxoffice=boxoffice_limited,
                               username=username)

    return render_template('box_office.html', boxoffice=boxoffice_limited)


@app.route('/randomize')
def randomize():
    '''Request a random movie title and redirect to its template.'''
    def random_title():
        titles = mongo.db.titles.find()
        titles_id_list = []
        for title in titles:
            imdb_id = title['imdb_id']
            titles_id_list.append(imdb_id)
        random_title_id = random.choice(titles_id_list)
        return random_title_id

    return redirect(url_for('title', title_id=random_title()))


@app.errorhandler(404)
def not_found(e):
    '''Render 404 error template'''
    if 'username' in session:
        username = session['username']
        return render_template("404.html", username=username)
    return render_template("404.html")


if __name__ == '__main__':
    app.run(host=os.environ.get('IP'),
            port=int(os.environ.get('PORT')),
            debug=bool(os.environ.get('DEBUG')))
