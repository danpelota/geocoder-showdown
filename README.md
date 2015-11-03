# Geocoder Showdown
An analysis of several popular geocoders, including:

* PostGIS Tiger Geocoder
* Nominatim
* SmartyStreets
* Google Maps

## Installing PostgreSQL 9.4, PostGIS 2.2, and the TIGER geocoder

First, I'll be setting the following PostgreSQL environment variables in `~/.profile`:

    export PGDATABASE=geocoder
    export PGUSER=postgres

(Don't forget to `source ~/.profile`.)

Next, add the PostgreSQL apt repo and key:

    sudo add-apt-repository "deb http://apt.postgresql.org/pub/repos/apt/ trusty-pgdg main"
    wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
    sudo apt-get update

Install PostgreSQL 9.4:

    sudo apt-get install -y postgresql-9.4

Install our PostGIS dependencies, as well as a few other spatial packages we'll need later:

    sudo apt-get install libxml2-dev libgeos-dev libproj-dev libpcre3-dev libxml2-dev libpq-dev postgresql-server-dev-9.4 g++ libgdal-dev python-gdal

We'll build PostGIS 2.2 against libgeos 3.5

    wget http://download.osgeo.org/geos/geos-3.5.0.tar.bz2
    tar xjf geos-3.5.0.tar.bz2
    cd geos-3.5.0/
    ./configure
    make
    sudo make install

We'll use PostGIS 2.2, which includes support for the 2015 vintage of the TIGER
GIS data:

    wget http://postgis.net/stuff/postgis-2.2.0dev.tar.gz
    tar xvf postgis-2.2.0dev.tar.gz
    cd postgis-2.2.0dev
    ./configure --without-raster
    make
    sudo make install
    cd ..

Create our PostGIS-enabled database and install the geocoder:

    createdb
    psql -c "CREATE EXTENSION postgis;"
    psql -c "CREATE EXTENSION fuzzystrmatch;"
    psql -c "CREATE EXTENSION address_standardizer;"
    psql -c "CREATE EXTENSION postgis_tiger_geocoder;"

Now we'll generate and run the scripts that download and process the FL TIGER
data, as well as the national state and county lookup tables needed by the
geocoder.

    # Required for the geocoder scripts
    sudo apt-get install unzip
    psql -t -c "SELECT Loader_Generate_Script(ARRAY['FL'], 'sh');" -o import-fl.sh --no-align
    # Go for a long walk
    sh import-fl.sh
    psql -t -c "SELECT loader_generate_nation_script('sh');" -o import-nation.sh --no-align
    sh import-nation.sh

Just for good measure:

    psql -c "SELECT install_missing_indexes();"
    psql -c "vacuum analyze verbose tiger.addr;"
    psql -c "vacuum analyze verbose tiger.edges;"
    psql -c "vacuum analyze verbose tiger.faces;"
    psql -c "vacuum analyze verbose tiger.featnames;"
    psql -c "vacuum analyze verbose tiger.place;"
    psql -c "vacuum analyze verbose tiger.cousub;
    psql -c "vacuum analyze verbose tiger.county;"
    psql -c "vacuum analyze verbose tiger.state;"

Check that the geocoder and all necessary data was installed correctly:

    psql -c "SELECT st_x(geomout), st_y(geomout) FROM geocode('400 S Monroe St, Tallahassee, FL 32399');"
