# Geocoder Showdown
An analysis of several popular geocoders, including:

* PostGIS Tiger Geocoder
* Nominatim
* SmartyStreets
* Geocommons Geocoder

## Installing PostgreSQL 9.4, PostGIS 2.2, and the TIGER geocoder

First, I'll be setting the following PostgreSQL environment variables:

    export PGDATABASE=geocoder
    export PGUSER=postgres

Next, add the PostgreSQL apt repo and key:

    sudo add-apt-repository "deb http://apt.postgresql.org/pub/repos/apt/ trusty-pgdg main"
    wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
    sudo apt-get update

Install PostgreSQL 9.4:

    sudo apt-get install -y postgresql-9.4

Install our PostGIS dependencies, as well as a few other spatial packages we'll need later:

    sudo apt-get install -y libxml2-dev libgeos-dev libproj-dev libpcre3-dev libxml2-dev libpq-dev postgresql-server-dev-9.4 g++ libgdal-dev python-gdal

We'll build PostGIS 2.2 against libgeos 3.5.

    wget http://download.osgeo.org/geos/geos-3.5.0.tar.bz2
    tar xjf geos-3.5.0.tar.bz2
    cd geos-3.5.0/
    ./configure
    make
    sudo make install
    cd ..

We'll use PostGIS 2.2, which includes support for the 2015 vintage of the TIGER
GIS data:

    wget http://postgis.net/stuff/postgis-2.2.0dev.tar.gz
    tar xvf postgis-2.2.0dev.tar.gz
    cd postgis-2.2.0dev
    ./configure
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
    sudo apt-get install -y unzip
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

## Installing the geocommons geocoder

Install some dependencies:

    sudo apt-get install -y ruby-dev sqlite3 libsqlite3-dev flex
    sudo gem install text sqlite3 fastercsv

Grab the latest version of the geocommons geocoder and install it:

    sudo apt-get install git
    git clone git://github.com/geocommons/geocoder.git
    cd geocoder
    make
    make install
    sudo gem install Geocoder-US-2.0.4.gem

We can use the 2015 Tiger data we downloaded previously. 

    mkdir data
    mkdir database
    cd data
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/ADDR/*.zip ./
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/FEATNAMES/*.zip ./
    cp /gisdata/ftp2.census.gov/geo/tiger/TIGER2015/EDGES/*.zip ./

Create the geocoder database. (Note that this must be executed from within the
`build` directory since it has a hard reference to
`../src/shp2sqlite/shp2sqlite`.)

    cd ../build
    ./tiger_import ../database/geocoder.db ../data
    sh ./build_indexes ../database/geocoder.db
    cd ..
    bin/rebuild_metaphones database/geocoder.db
    sudo sh build/rebuild_cluster database/geocoder.db

To test the geocommons geocoder, fire up an irb session and geocode a test address:

    irb(main):001:0> require 'geocoder/us'
    => true

    irb(main):002:0> db = Geocoder::US::Database.new('database/geocoder.db')
    => #<Geocoder::US::Database:0x00000001cc1248 @db=#<SQLite3::Database:0x00000001cc1158>, @st={}, @dbtype=1, @debug=false, @threadsafe=false>

    irb(main):003:0> p db.geocode("400 S Monroe St, Tallahassee, FL 32399")
    [{:zip=>"32399", :city=>"Tallahassee", :state=>"FL", :lat=>30.436901,
      :lon=>-84.282546, :fips_county=>"12073", :score=>0.614, :precision=>:zip}]
    => [{:zip=>"32399", :city=>"Tallahassee", :state=>"FL", :lat=>30.436901,
         :lon=>-84.282546, :fips_county=>"12073", :score=>0.614, :precision=>:zip}]

## Installing the benchmark data: Florida statewide parcel data

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

These shapefiles come in several different projections, so we'll determine the
projection for each file via the `get_srid.py` script, which simply feeds the
contents of the .prj file to prj2epsg.org and returns the srid to `STDOUT`.
Once we determine the SRID, we'll use `shp2pgsql` to stick the GIS data into a
Postgres table. Finally, we'll reproject the geometry column into SRID 4326 and
cram all the counties into a single table:

    psql << EOF 
    DROP TABLE IF EXISTS props_gis;
    CREATE TABLE props_gis (
        gid SERIAL PRIMARY KEY,
        parcelno TEXT,
        geom GEOMETRY('MULTIPOLYGON', 4326)
    );
    EOF

    for FILE in *.shp
    do
        echo "Getting SRID for $FILE"
        #TODO: Replace this with a simple curl call?
        SRID=$(python get_srid.py $FILE)
        echo "SRID for $FILE is $SRID"
        psql -c "DROP TABLE IF EXISTS staging.props_gis;"
        shp2pgsql -s $SRID -c "$FILE" staging.props_gis | psql --quiet
        psql -c "INSERT INTO props_gis (parcelno, geom) SELECT parcelno, ST_Transform(ST_Force2D(geom), 4326) FROM staging.props_gis;"
    done
    
    psql -c "CREATE INDEX idx_props_gis_parcelno ON props_gis(parcelno);"

Next, we need to download and import the NAL data.

    cd /gisdata/flprop/nal
    wget -N ftp://sdrftp03.dor.state.fl.us/Tax%20Roll%20Data%20Files/2015%20Preliminary%20NAL%20-%20SDF%20Files/*NAL*.zip
    unzip -u -j "*.zip"

    # Table to hold the property data
    psql << EOF
    CREATE SCHEMA IF NOT EXISTS staging;
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
        psql -c "\copy staging.props_nal FROM $file CSV HEADER NULL 'thereisnonull'"
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

    CREATE INDEX idx_props_nal_parcel_id ON props_nal(parcel_id);

    -- Some properties are missing a phy_city and/or phy_zipcd. We can infer them
    -- from the owner's information, where the owner has the same address
    UPDATE props_nal
    SET
        phy_city = own_city,
        phy_zipcd = own_zipcd
    WHERE
        own_addr1 = phy_addr1
        AND own_addr1 != ''
        AND (phy_zipcd = '' OR own_zipcd = '')
        AND own_zipcd != ''
        AND own_city != '';
    EOF

Next, we'll create a sample dataset of 10,000 properties to use as our
reference:

    psql << EOF
    SELECT set_seed(.50);
    CREATE TABLE props_sampled AS
    SELECT
        n.parcel_id,
        btrim(n.phy_addr1 || ' ' || n.phy_addr2) as street,
        n.phy_city as city,
        n.phy_zipcd as zip,
        'FL' as state,
        g.geom
    FROM
        props_nal n JOIN props_gis g ON n.parcel_id = g.parcelno
    WHERE
        phy_addr1 != '' AND phy_city != '' AND phy_zipcd != ''
    ORDER BY random()
    LIMIT 10000;

    CREATE INDEX idx_props_sampled_geom ON props_sampled USING gist(geom);
    EOF
