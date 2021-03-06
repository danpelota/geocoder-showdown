Geocoder Showdown
=================

Back in 2011, [I asked a question](http://gis.stackexchange.com/questions/7271/geocode-quality-nominatim-vs-postgis-geocoder-vs-geocoderus-2-0) 
on gis.stackexchange.com regarding the accuracy of range-based geocoders that
can be installed and run locally. Since then, I've leveraged several solutions
for bulk geocoding, including the [PostGIS geocoder](http://postgis.net/docs/Geocode.html),
the ruby-based [Geocommons Geocoder](https://github.com/geocommons/geocoder/), 
and [SmartyStreets](https://smartystreets.com/) (which doesn't run locally, but
has no trouble geocoding millions of addresses per hour). However, I haven't
come across a thorough analysis of the accuracy of these geocoders, and the
stackexchange question still receives attention, so I figured
I'd evaluate them here. 

First, I'll run through the installation of the PostGIS Tiger geocoder, the
Nominatim geocoder (i.e. OpenStreetMaps's geocoder/reverse geocoder), and the
Geocommons Geocoder. While there are web services that expose each through 
APIs, I wanted to review the setup and installation here as well.

Then, I'll evaluate each against a test dataset: the Florida Statewide Property
Database. I'll also evaluate SmartyStreets, which offers CASS-certified address
standardization/validation through a web API.

I'll install and evaluate the geocoders on an `m4.xlarge` AWS EC2 instance with
16GB of memory and a 50GB SSD, running the Ubuntu 14.04 AMI (ami-5189a661).

Installing the Geocoders
========================

PostgreSQL 9.4, PostGIS 2.2, and the TIGER geocoder
---------------------------------------------------

First, we'll set the following PostgreSQL environment variables:

    export PGDATABASE=geocoder
    export PGUSER=postgres

Next, add the PostgreSQL apt repo and key:

    sudo add-apt-repository "deb http://apt.postgresql.org/pub/repos/apt/ trusty-pgdg main"
    wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
    sudo apt-get update

Install PostgreSQL 9.4:

    sudo apt-get install -y postgresql-9.4
    export PATH='/usr/lib/postgresql/9.4/bin/':$PATH

You'll want to edit the PostgreSQL config file
(/etc/postgresql/9.4/main/postgresql.conf on Ubuntu) for optimum performance in
bulk-loading data. Here's how I tuned my PostgreSQL cluster running on an
instance with 16GB of RAM:

* `shared_buffers = 4GB`
* `work_mem = 50MB`
* `maintenance_work_mem = 4GB`
* `synchronous_commit = off`
* `checkpoint_segments = 100`
* `checkpoint_timeout = 10min`
* `checkpoint_completion_target = 0.9`
* `effective_cache_size = 12GB`

I've also set:
* `fsync = off`
* `full_page_writes = off`

Be sure to turn these on after the data has been loaded, or you'll risk not
only data _loss_ in the event of a crash, but data _corruption_.

Also, before connecting to our database, you'll need to edit the `pg_hba.conf` file
to `trust` local connections.

Restart PostgreSQL and we're ready to install PostGIS.

Install the PostGIS dependencies, as well as a few other spatial packages we'll
need later:

    apt-get install -y libxml2-dev libgeos-dev libproj-dev libpcre3-dev  
    apt-get install -y liblwgeom-dev libpq-dev postgresql-server-dev-9.4 g++ gcc
    apt-get install -y libgdal-dev python-gdal python-requests unzip make

We'll build PostGIS 2.2 against libgeos 3.5.

    cd ~
    wget http://download.osgeo.org/geos/geos-3.5.0.tar.bz2
    tar xjf geos-3.5.0.tar.bz2
    cd geos-3.5.0/
    ./configure
    make
    sudo make install
    cd ..

We'll use PostGIS 2.2, which includes support for the 2015 vintage of the TIGER
GIS data:

    cd ~
    wget http://postgis.net/stuff/postgis-2.2.0dev.tar.gz
    tar xvf postgis-2.2.0dev.tar.gz
    cd postgis-2.2.0dev
    ./configure
    make
    sudo make install
    cd ..

Create our PostGIS-enabled database and install the geocoder.

    createdb
    psql -c "CREATE EXTENSION postgis;"
    psql -c "CREATE EXTENSION fuzzystrmatch;"
    psql -c "CREATE EXTENSION address_standardizer;"
    psql -c "CREATE EXTENSION postgis_tiger_geocoder;"

Now we'll generate and run the scripts that download and process the FL TIGER
data, as well as the national state and county lookup tables needed by the
geocoder.

    cd ~
    sudo mkdir /gisdata
    sudo chown ubuntu /gisdata
    psql -t -c "SELECT Loader_Generate_Script(ARRAY['FL'], 'sh');" -o import-fl.sh --no-align
    sh import-fl.sh
    # Go for a long walk
    psql -t -c "SELECT loader_generate_nation_script('sh');" -o import-nation.sh --no-align
    sh import-nation.sh

Just for good measure:

    psql -c "SELECT install_missing_indexes();"
    psql -c "vacuum analyze verbose tiger.addr;"
    psql -c "vacuum analyze verbose tiger.edges;"
    psql -c "vacuum analyze verbose tiger.faces;"
    psql -c "vacuum analyze verbose tiger.featnames;"
    psql -c "vacuum analyze verbose tiger.place;"
    psql -c "vacuum analyze verbose tiger.cousub;"
    psql -c "vacuum analyze verbose tiger.county;"
    psql -c "vacuum analyze verbose tiger.state;"

Check that the geocoder and all necessary data was installed correctly:

    psql -c "SELECT st_x(geomout), st_y(geomout) FROM geocode('400 S Monroe St, Tallahassee, FL 32399', 1);"

           st_x        |       st_y
    -------------------+------------------
     -84.2807360244119 | 30.4381207774995
    (1 row)

The Geocommons Geocoder
-----------------------

Install some dependencies:

    apt-get install -y ruby-dev sqlite3 libsqlite3-dev flex
    gem install text sqlite3 fastercsv

Grab the latest version of the geocommons geocoder and install it:

    cd ~
    apt-get install git flex ruby-dev
    git clone git://github.com/geocommons/geocoder.git
    cd geocoder
    make
    make install
    gem install Geocoder-US-2.0.4.gem
    gen install text

We can use the 2015 Tiger data we downloaded previously. 

    mkdir data
    mkdir database
    cd data
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/ADDR/*.zip ./
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/FEATNAMES/*.zip ./
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/EDGES/*.zip ./

Create the geocoder database. Note that this must be executed from within the
`build` directory since it has a relative path reference to
`../src/shp2sqlite/shp2sqlite`.

    cd ../build
    ./tiger_import ../database/geocoder.db ../data
    sh build_indexes ../database/geocoder.db
    cd ..
    bin/rebuild_metaphones database/geocoder.db
    sudo sh build/rebuild_cluster database/geocoder.db

To test the geocommons geocoder, fire up an irb session and geocode a test address:

    irb(main):001:0> require 'geocoder/us'
    => true

    irb(main):002:0> db = Geocoder::US::Database.new('database/geocoder.db')
    => #<Geocoder::US::Database:0x00000001cc1248 @db=#<SQLite3::Database:0x00000001cc1158>, @st={}, @dbtype=1, @debug=false, @threadsafe=false>

    irb(main):003:0> p db.geocode("400 S Monroe St, Tallahassee, FL 32399")
    [{:street=>"S Monroe St",
      :zip=>"32301",
      :score=>0.805, 
      :prenum=>"", 
      :number=>"400", 
      :precision=>:range, 
      :lon=>-84.280632, 
      :lat=>30.438122}]

Installing Nominatim
--------------------

Install the Nominatim dependencies (some of these were installed in previous
steps, but are included here for completeness):

    apt-get install -y build-essential libxml2-dev libgeos-dev libpq-dev libbz2-dev 
    apt-get install -y libtool automake libproj-dev libboost-dev 
    apt-get install -y libboost-system-dev libboost-filesystem-dev libboost-thread-dev 
    apt-get install -y libexpat-dev gcc proj-bin libgeos-c1 osmosis libgeos++-dev
    apt-get install -y php5 php-pear php5-pgsql php5-json php-db
    apt-get install -y libprotobuf-c0-dev protobuf-c-compiler

Download and install Nominatim

    cd ~
    wget http://www.nominatim.org/release/Nominatim-2.4.0.tar.bz2
    tar xvf Nominatim-2.4.0.tar.bz2
    cd Nominatim
    ./configure
    make

Update the nominatim php settings (`settings/settings.php`) to reflect our
version of PostgreSQL, PostGIS, and our local website URL:


    // Software versions
    @define('CONST_Postgresql_Version', '9.4'); // values: 9.0, ... , 9.4
    @define('CONST_Postgis_Version', '2.2'); // values: 1.5, 2.0, 2.1

    // Website settings
    @define('CONST_Website_BaseURL', '/nominatim/');

Create our web user:

    createuser -SDR www-data

The module path needs to be executable: 

    chmod +x ./
    chmod +x module

Download and load the Florida OSM data:

    wget -P /gisdata/ http://download.geofabrik.de/north-america/us/florida-latest.osm.pbf
     ./utils/setup.php --osm-file /gisdata/florida-latest.osm.pbf --all  2>&1 | tee setup.log

We'll use nginx as our webserver:

    sudo apt-get install nginx php5-fpm

Setup the local nominatim web service

    sudo mkdir -m 755 /usr/share/nginx/html/nominatim
    sudo chown ubuntu /usr/share/nginx/html/nominatim
    ./utils/setup.php --create-website /usr/share/nginx/html/nominatim

Edit `/etc/nginx/sites-available/default` to include:

    location ~ [^/]\.php(/|$) {
           fastcgi_split_path_info ^(.+?\.php)(/.*)$;
           if (!-f $document_root$fastcgi_script_name) {
                   return 404;
           }
           fastcgi_pass unix:/var/run/php5-fpm.sock;
           fastcgi_index index.php;
           include fastcgi_params;
    }

At this point, you should be able to point your browser to
`http://localhost/nominatim/index.php` and browse your locally hosted copy
of the OSM website, including the nominatim geocoder. Let's run a test address:

    curl "http://localhost/nominatim/search.php?q=400%20S%20Monroe%20St%2C%20Tallahassee%2C%20FL%2032399&format=json"

    [{"place_id":"666756","licence":"Data © OpenStreetMap contributors, ODbL 1.0. http:\/\/www.openstreetmap.org\/copyright","osm_type":"way","osm_id":"84737196","boundingbox":["30.4345605","30.4354982","-84.2807049","-84.2806661"],"lat":"30.4349352","lon":"-84.2807049","display_name":"South Monroe Street, Tallahassee, Leon County, Florida, 32399-6508, United States of America","class":"highway","type":"primary","importance":0.61},{"place_id":"641941","licence":"Data © OpenStreetMap contributors, ODbL 1.0. http:\/\/www.openstreetmap.org\/copyright","osm_type":"way","osm_id":"47976800","boundingbox":["30.4362501","30.4380588","-84.2806933","-84.2806236"],"lat":"30.4374929","lon":"-84.2806933","display_name":"South Monroe Street, Tallahassee, Leon County, Florida, 32301-2034, United States of America","class":"highway","type":"primary","importance":0.61}]

Nominatim can use TIGER address data to supplement the OSM house number data.
Luckily, we already have the TIGER EDGE data downloaded. First, we'll need to
convert the data to SQL:

    ./utils/imports.php --parse-tiger-2011 /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/EDGES/

Then we'll load it:

    ./utils/setup.php --import-tiger-data

Benchmark Data: Florida Statewide Property Data
===============================================

We'll evaluate the accuracy of these geocoders against GIS data representing
every parcel in Florida. This data serves as a suitable reference since it:

* is relatively complete
* is readily accessible
* includes the full spectrum of urbanicity, from very rural to very urban locations
* includes both long-standing and newly-assigned addresses
* includes a wide range of address patterns

In addition to the shapefiles, we'll need the NAL data (name, address,
location), which will provide the physical street address for each parcel in
the shapefiles:

    mkdir -p /gisdata/flprop/gis
    mkdir -p /gisdata/flprop/nal
    chmod 777 -R /gisdata/flprop
    cd /gisdata/flprop/gis
    wget -N ftp://sdrftp03.dor.state.fl.us/Map%20Data/00_2015/*.zip
    unzip -u -j "*.zip"

These shapefiles come in various projections, so we'll determine the
projection dynamically for each file via the `get_srid.py` script, which simply
feeds the contents of the .prj file to prj2epsg.org and returns the srid to
`STDOUT`.  Once we determine the SRID, we'll use `shp2pgsql` to stick the GIS
data into a Postgres table. Finally, we'll reproject the geometry column into
SRID 4326 and cram all the counties into a single table:

    psql << EOF
    DROP TABLE IF EXISTS props_gis;
    CREATE TABLE props_gis (
        gid SERIAL PRIMARY KEY,
        parcelno TEXT,
        geom GEOMETRY('MULTIPOLYGON', 4326)
    );
    CREATE SCHEMA IF NOT EXISTS staging;
    EOF

    for FILE in *.shp
    do
        echo "Getting SRID for $FILE"
        SRID=$(python get_srid.py $FILE)
        echo "SRID for $FILE is $SRID"
        psql -c "DROP TABLE IF EXISTS staging.props_gis;"
        shp2pgsql -s $SRID -c "$FILE" staging.props_gis | psql --quiet
        psql -c "INSERT INTO props_gis (parcelno, geom) SELECT parcelno, ST_Transform(ST_Force2D(geom), 4326) FROM staging.props_gis;"
    done
        
    psql -c "CREATE INDEX idx_props_gis_parcelno ON props_gis(parcelno);"

Next, we need to download and import the NAL data.

    cd /gisdata/flprop/nal
    wget -N ftp://sdrftp03.dor.state.fl.us/Tax%20Roll%20Data%20Files/2015%20Final%20NAL-SDF%20Files/*NAL*.zip
    unzip -u -j "*.zip"

    # Table to hold the property data
    psql << EOF
    DROP TABLE IF EXISTS staging.props_nal;
    CREATE TABLE staging.props_nal(
        co_no TEXT,
        parcel_id TEXT,
        file_t TEXT,
        asmnt_yr TEXT,
        bas_strt TEXT,
        atv_strt TEXT,
        grp_no TEXT,
        dor_uc TEXT,
        pa_uc TEXT,
        spass_cd TEXT,
        jv TEXT,
        jv_chng TEXT,
        jv_chng_cd TEXT,
        av_sd TEXT,
        av_nsd TEXT,
        tv_sd TEXT,
        tv_nsd TEXT,
        jv_hmstd TEXT,
        av_hmstd TEXT,
        jv_non_hmstd_resd TEXT,
        av_non_hmstd_resd TEXT,
        jv_resd_non_resd TEXT,
        av_resd_non_resd TEXT,
        jv_class_use TEXT,
        av_class_use TEXT,
        jv_h2o_rechrge TEXT,
        av_h2o_rechrge TEXT,
        jv_consrv_lnd TEXT,
        av_consrv_lnd TEXT,
        jv_hist_com_prop TEXT,
        av_hist_com_prop TEXT,
        jv_hist_signf TEXT,
        av_hist_signf TEXT,
        jv_wrkng_wtrfnt TEXT,
        av_wrkng_wtrfnt TEXT,
        nconst_val TEXT,
        del_val TEXT,
        par_splt TEXT,
        distr_cd TEXT,
        distr_yr TEXT,
        lnd_val TEXT,
        lnd_unts_cd TEXT,
        no_lnd_unts TEXT,
        lnd_sqfoot TEXT,
        dt_last_inspt TEXT,
        imp_qual TEXT,
        const_class TEXT,
        eff_yr_blt TEXT,
        act_yr_blt TEXT,
        tot_lvg_area TEXT,
        no_buldng TEXT,
        no_res_unts TEXT,
        spec_feat_val TEXT,
        multi_par_sal1 TEXT,
        qual_cd1 TEXT,
        vi_cd1 TEXT,
        sale_prc1 TEXT,
        sale_yr1 TEXT,
        sale_mo1 TEXT,
        or_book1 TEXT,
        or_page1 TEXT,
        clerk_no1 TEXT,
        sal_chng_cd1 TEXT,
        multi_par_sal2 TEXT,
        qual_cd2 TEXT,
        vi_cd2 TEXT,
        sale_prc2 TEXT,
        sale_yr2 TEXT,
        sale_mo2 TEXT,
        or_book2 TEXT,
        or_page2 TEXT,
        clerk_no2 TEXT,
        sal_chng_cd2 TEXT,
        own_name TEXT,
        own_addr1 TEXT,
        own_addr2 TEXT,
        own_city TEXT,
        own_state TEXT,
        own_zipcd TEXT,
        own_state_dom TEXT,
        fidu_name TEXT,
        fidu_addr1 TEXT,
        fidu_addr2 TEXT,
        fidu_city TEXT,
        fidu_state TEXT,
        fidu_zipcd TEXT,
        fidu_cd TEXT,
        s_legal TEXT,
        app_stat TEXT,
        co_app_stat TEXT,
        mkt_ar TEXT,
        nbrhd_cd TEXT,
        public_lnd TEXT,
        tax_auth_cd TEXT,
        twn TEXT,
        rng TEXT,
        sec TEXT,
        census_bk TEXT,
        phy_addr1 TEXT,
        phy_addr2 TEXT,
        phy_city TEXT,
        phy_zipcd TEXT,
        alt_key TEXT,
        ass_trnsfr_fg TEXT,
        prev_hmstd_own TEXT,
        ass_dif_trns TEXT,
        cono_prv_hm TEXT,
        parcel_id_prv_hmstd TEXT,
        yr_val_trnsf TEXT,
        exmpt_01 TEXT,
        exmpt_02 TEXT,
        exmpt_03 TEXT,
        exmpt_04 TEXT,
        exmpt_05 TEXT,
        exmpt_06 TEXT,
        exmpt_07 TEXT,
        exmpt_08 TEXT,
        exmpt_09 TEXT,
        exmpt_10 TEXT,
        exmpt_11 TEXT,
        exmpt_12 TEXT,
        exmpt_13 TEXT,
        exmpt_14 TEXT,
        exmpt_15 TEXT,
        exmpt_16 TEXT,
        exmpt_17 TEXT,
        exmpt_18 TEXT,
        exmpt_19 TEXT,
        exmpt_20 TEXT,
        exmpt_21 TEXT,
        exmpt_22 TEXT,
        exmpt_23 TEXT,
        exmpt_24 TEXT,
        exmpt_25 TEXT,
        exmpt_26 TEXT,
        exmpt_27 TEXT,
        exmpt_28 TEXT,
        exmpt_29 TEXT,
        exmpt_30 TEXT,
        exmpt_31 TEXT,
        exmpt_32 TEXT,
        exmpt_33 TEXT,
        exmpt_34 TEXT,
        exmpt_35 TEXT,
        exmpt_36 TEXT,
        exmpt_37 TEXT,
        exmpt_38 TEXT,
        exmpt_39 TEXT,
        exmpt_40 TEXT,
        exmpt_80 TEXT,
        exmpt_81 TEXT,
        seq_no TEXT,
        rs_id TEXT,
        mp_id TEXT,
        state_par_id TEXT,
        spc_cir_cd TEXT,
        spc_cir_yr TEXT,
        spc_cir_txt TEXT
    );
    EOF

    for file in *.csv
    do
        echo "Loading $file"
        psql -c "\copy staging.props_nal FROM $file CSV HEADER"
    done

    psql << EOF
    DROP TABLE IF EXISTS props_nal;
    CREATE TABLE props_nal AS
    SELECT
        co_no,
        parcel_id,
        own_addr1,
        own_addr2,
        own_city,
        own_state,
        own_zipcd,
        own_state_dom,
        nbrhd_cd,
        phy_addr1,
        phy_addr2,
        phy_city,
        phy_zipcd,
        state_par_id
    FROM
        staging.props_nal;
    DROP TABLE staging.props_nal;

    CREATE INDEX idx_props_nal_parcel_id ON props_nal(parcel_id);
    VACUUM ANALYZE props_nal;

    -- Some properties are missing a phy_city and/or phy_zipcd. We can infer them
    -- from the owner's information, where the owner has the same address
    UPDATE props_nal
    SET
        phy_city = own_city,
        phy_zipcd = own_zipcd
    WHERE
        own_addr1 = phy_addr1
        AND own_addr1 != ''
        AND (phy_zipcd = '' OR phy_city = '')
        AND own_zipcd != ''
        AND own_city != '';
    EOF

Next, we'll create a sample dataset of 10,000 properties to use as our
reference. To ensure we have reasonable-looking addresses, we'll only include
those whose street address starts with a digit between 1 and 9. Furthermore,
some properties have duplicated parcel numbers, so we'll exclude those:

    psql << EOF
    SELECT setseed(.50);
    CREATE TABLE props_sampled AS
    SELECT
        n.parcel_id,
        btrim(COALESCE(n.phy_addr1, '') || ' ' || COALESCE(n.phy_addr2, '')) as street,
        COALESCE(n.phy_city, '') as city,
        COALESCE(n.phy_zipcd, '') as zip,
        'FL'::text as state,
        g.geom
    FROM
        props_nal n JOIN props_gis g ON n.parcel_id = g.parcelno
    WHERE
        phy_addr1 ~ '^[1-9]'
        AND phy_city ~* '^[A-Z]'
        AND phy_zipcd LIKE '3%'
        AND n.parcel_id NOT IN (
            SELECT parcel_id FROM props_nal GROUP BY 1 HAVING count(*) > 1)
        AND n.parcel_id NOT IN (
            SELECT parcelno FROM props_gis GROUP BY 1 HAVING count(*) > 1)
    ORDER BY random()
    LIMIT 10000;

    CREATE INDEX idx_props_sampled_geom ON props_sampled USING gist(geom);
    VACUUM ANALYZE props_sampled;
    EOF

There are a few properties statewide that have duplicated parcel numbers. For
now, we'll just delete them:

    DELETE FROM props_sampled
    WHERE parcel_id IN
        (SELECT parcel_id
         FROM props_sampled
         GROUP BY 1
         HAVING count(*) > 1);


Geocoding
=========

Geocoding with PostGIS
----------------------

    ALTER TABLE props_sampled
    ADD COLUMN postgis_geom geometry('POINT', 4326),
    ADD COLUMN postgis_rating integer;

    CREATE INDEX props_sampled_address ON props_sampled(street, city, zip, state);
    VACUUM ANALYZE props_sampled;

    UPDATE props_sampled
    SET
        postgis_geom = t.geomout,
        postgis_rating = t.rating
    FROM
        (SELECT
             street, city, state, zip,
             ST_Transform((g.geo).geomout, 4326) as geomout,
             (g.geo).rating as rating
         FROM
            (SELECT
                street, city, state, zip,
                geocode(street || ', ' || city || ' ' || state || ' ' || zip, 1) as geo
             FROM props_sampled
            ) g
        ) t
    WHERE p.street = t.street
        AND p.city = t.city
        AND p.state = t.state
        AND p.zip = t.zip;
