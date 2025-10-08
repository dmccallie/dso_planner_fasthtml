"""
    David McCallie
    Code to take raw data extracted from Stellarium's open source base and prepare it for use in Sveltekit DSO_APP
    Import into SQLite, massage it, and export as JSON for inclusion into DSO_APP
    Based on earlier code for Google Sheet version of the app
    Initial hacks 10Mar2023

    Updated Sep2025 for conversion to FastHTML version

"""
import sqlite3
import csv
import re
from dataclasses import dataclass
import json

"""
//convert RA degrees to h:m:s
function degree2HMS(degrees) {
  var hours_full = degrees / 15.0  //15 degrees per hour (24h = 360)
  var hours = Math.floor(hours_full)
  var mins_full = (hours_full - hours) * 60
  var mins = Math.floor(mins_full)
  var secs = Math.round((mins_full - mins) * 60)
  let reply = `${hours}H ${mins}M ${secs}S`
  return reply
}

//convert DEC degrees to d:m:s
function degree2DMS(degrees) {
  var degs = Math.floor(degrees)
  var mins_full = (degrees - degs) * 60
  var mins = Math.floor(mins_full)
  var secs = Math.round((mins_full - mins) * 60)
  let reply = `${degs}° ${mins}' ${secs}"`
  return reply
}
"""
import math

def degree2HMS(dec_degrees):
    #convert RA in decimal degrees to string form of HMS
    hours_full = dec_degrees / 15.0
    hours = math.floor(hours_full)
    mins_full = (hours_full - hours) * 60
    mins = math.floor(mins_full)
    secs = (mins_full - mins) * 60
    return f"{hours}h {mins}m {secs:.1f}s"

def degree2DMS(dec_degrees):
    #convert DEC in decimal degrees to string from of DMS
    degs = math.floor(dec_degrees)
    mins_full = (dec_degrees - degs) * 60
    mins = math.floor(mins_full)
    secs = (mins_full - mins) * 60
    return f"{degs}° {mins}' {secs:.1f}" + '\"'  #results in "24° 7' 1.2""" which works for Google Sheets??

def HMS2Degrees(hms):
    #convert HMS string to decimal degrees
    #hms is a string like "12h 34m 56.7s"
    #returns a float

    #first split into hours, minutes, seconds, on spaces
    hms_list = hms.split()
    hours = float(hms_list[0][:-1]) #drop the trailing 'h'
    minutes = float(hms_list[1][:-1]) #drop the trailing 'm'
    seconds = float(hms_list[2][:-1]) #drop the trailing 's'
    return 15.0 * (hours + minutes/60.0 + seconds/3600.0)

def DMS2Degrees(dms):
    #convert DMS string to decimal degrees
    #dms is a string like "12° 34' 56.7"
    #returns a float
    
    #first split into degrees, minutes, seconds, on spaces
    dms_list = dms.split()
    degrees = float(dms_list[0][:-1]) #drop the trailing '°'
    minutes = float(dms_list[1][:-1]) #drop the trailing '
    seconds = float(dms_list[2][:-1]) #drop the trailing "
    return degrees + minutes/60.0 + seconds/3600.0



def valid_cat_id(num):
    if num == "0" or num == None or len(num) == 0:
        return False
    return True

def create_tables(conn):
    #table to hold details about a given DSO
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS dso_detail;')
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dso_detail (
            dso_id          INTEGER PRIMARY KEY NOT NULL,
            ra_dd           FLOAT   NOT NULL,
            dec_dd          FLOAT   NOT NULL,
            maj_axis        FLOAT   NOT NULL,
            min_axis        FLOAT   NOT NULL,
            orient_angle    FLOAT,
            v_mag           FLOAT   NOT NULL,
            obj_type        TEXT,
            morph_type      TEXT,
            distance        FLOAT
        );
        """)

    #table to hold catalog + identifier and map to dso_detail
    cur.execute('DROP TABLE IF EXISTS catalog_dso;')
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_dso (
            catalog_id      INTEGER PRIMARY KEY,
            dso_id          INTEGER     NOT NULL,
            catalog_name    TEXT    NOT NULL collate nocase,
            catalog_num     TEXT    NOT NULL collate nocase,
            FOREIGN KEY (dso_id) REFERENCES dso_detail (dso_id)
        );
        """)
    
    #table to hold common names, indexed via catalog
    cur.execute('DROP TABLE IF EXISTS names;')
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS names (
            name_id         INTEGER PRIMARY KEY,
            catalog_name    TEXT    NOT NULL collate nocase,
            catalog_num     TEXT    NOT NULL collate nocase,
            name            TEXT    NOT NULL collate nocase,
            catalog_id      INTEGER NOT NULL,
            dso_id          INTEGER NOT NULL,  
            FOREIGN KEY (catalog_id) REFERENCES catalog_dso (catalog_id)
            FOREIGN KEY (dso_id) REFERENCES dso_detail (dso_id)
        );
        """)

    #table to hold collections of interesting DSOs
    #not properly normalized, but it's so small...
    cur.execute('DROP TABLE IF EXISTS collections;')
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            collection_id      INTEGER PRIMARY KEY,
            collection_name    TEXT    NOT NULL collate nocase,
            catalog_id         INTEGER NOT NULL,
            dso_id             INTEGER NOT NULL,
            FOREIGN KEY (catalog_id) REFERENCES catalog_dso (catalog_id),
            FOREIGN KEY (dso_id) REFERENCES dso_detail (dso_id)
        );
        """)

    return

def insert_dso_and_catalog(conn, data, cat_keeper_set):
    #insert dso into dso_details and then add selected catalog identifiers to catalog_dso child table

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dso_detail (dso_id, ra_dd, dec_dd, maj_axis, min_axis, orient_angle, v_mag, 
            obj_type, morph_type, distance )
            VALUES (?,?,?,?,?,?,?,?,?,?);
        """, 
        (data['dso_id'], data['ra_dd'], data['dec_dd'], data['maj_axis'], data['min_axis'],
         data['orient_angle'], data['v_mag'], data['obj_type'], data['morph_type'], data['distance']))
    
    #dso_row_id = cursor.lastrowid

    #now for each catalog that we are tracking, insert catalog details linked back to dso
    for cat in cat_keeper_set:
        if valid_cat_id(data[cat]):
            cur.execute(
                """
                INSERT INTO catalog_dso(dso_id, catalog_name, catalog_num)
                    VALUES (?,?,?);
                """,
                (data['dso_id'], cat, data[cat])
            )

def insert_names_and_collections(conn, catalog_name, catalog_num, name, collections_list):
    
    cur = conn.cursor()
    #first find the catalog_id for the fk to catalog_dso
    #actually lets find the dso_id - don't really care about the particular catalog, right?
    # a denormalization for convenience?
    #cur.execute("""
    #    SELECT catalog_id FROM catalog_dso cd
    #        WHERE cd.catalog_name = ? and cd.catalog_num = ?
    #    """, (catalog_name, catalog_num))    cur.execute("""

    cur.execute("""
        SELECT cd.catalog_id, dd.dso_id FROM catalog_dso cd, dso_detail dd
            WHERE cd.catalog_name = ? and cd.catalog_num = ? and
            cd.dso_id = dd.dso_id
        """, (catalog_name, catalog_num))

    row = cur.fetchone() 
    if row is None:
        print(f"Could not find any catalog entry for {catalog_name} {catalog_num}")
        return
    
    #insert new row into name table
    catalog_id = row[0]
    dso_id = row[1]
    cur.execute(
        """
        INSERT INTO names (catalog_name, catalog_num, name, catalog_id, dso_id )
            VALUES (?,?,?,?,?);
        """, 
        (catalog_name, catalog_num, name, catalog_id, dso_id))

    #and add collections entries (if any) to the collections table
    #denormalize the dso_id to make the SQL easier?
    if collections_list and len(collections_list) > 0:
        coll_records = [ (coll.strip(), catalog_id, dso_id) for coll in collections_list.split(',')]

        cur.executemany(
            "INSERT INTO collections (collection_name, catalog_id, dso_id) VALUES (?,?, ?)", coll_records)
        
    
    return

def load_stellarium_catalog(conn, filename):
    #load the main DSO catalog - lots of tab-separated fields - data starts after "# --- DATA ---"
    # Generic columns:
    #  1 - (int)	- deep-sky object identificator
    #  2 - (float)	- RA (decimal degrees)
    #  3 - (float)	- Dec (decimal degrees)
    #  4 - (float)	- B magnitude
    #  5 - (float)	- V magnitude
    #  6 - (string)	- Object type (G, GX, GC, OC, NB, PN, DN, RN, C+N, HA, HII, SNR, BN, EN, SA, SC, RG, CL, IG, QSO or empty)
    #  7 - (string)	- Morphological type of object
    #  8 - (float)	- Major axis size or radius (arcmin)
    #  9 - (float)	- Minor axis size (arcmin)
    # 10 - (int)	- Orientation angle (degrees)
    # 11 - (float)	- Redshift
    # 12 - (float)	- Error of redshift
    # 13 - (float)	- Parallax (mas)
    # 14 - (float)	- Error of parallax (mas)
    # 15 - (float)	- Non-redshift distance (kpc)
    # 16 - (float)	- Error of non-redsift distance (kpc)
    # Cross-index columns:
    # 17 - (int)	- NGC number (New General Catalogue)
    # 18 - (int)	- IC number (Index Catalogue)
    # 19 - (int)	- M number (Messier Catalog)
    # 20 - (int)	- C number (Caldwell Catalogue)
    # 21 - (int)	- B number (Barnard Catalogue)
    # 22 - (int)	- Sh2 number (Sharpless Catalogue)
    # 23 - (int)	- VdB number (Van den Bergh Catalogue of reflection nebulae)
    # 24 - (int)	- RCW number (A catalogue of Hα-emission regions in the southern Milky Way)
    # 25 - (int)	- LDN number (Lynds' Catalogue of Dark Nebulae)
    # 26 - (int)	- LBN number (Lynds' Catalogue of Bright Nebulae)
    # 27 - (int)	- Cr number (Collinder Catalogue)
    # 28 - (int)	- Mel number (Melotte Catalogue of Deep Sky Objects)
    # 29 - (int)	- PGC number (HYPERLEDA. I. Catalog of galaxies)
    # 30 - (int)	- UGC number (The Uppsala General Catalogue of Galaxies)
    # 31 - (string)	- Ced number (Cederblad Catalog of bright diffuse Galactic nebulae)
    # 32 - (int)	- Arp number (Atlas of Peculiar Galaxies)
    # 33 - (int)	- VV number (The catalogue of interacting galaxies by Vorontsov-Velyaminov)
    # 34 - (string)	- PK identificator (Catalogue of Galactic Planetary Nebulae)
    # 35 - (string)	- PN G identificator (Strasbourg-ESO Catalogue of Galactic Planetary Nebulae)
    # 36 - (string)	- SNR G identificator (A catalogue of Galactic supernova remnants)
    # 37 - (string)	- ACO number (Rich Clusters of Galaxies by Abell et. al.)
    # 38 - (string)	- HCG identificator (Hickson Compact Group by Hickson P.)
    # 39 - (string)	- ESO identificator (ESO/Uppsala survey of the ESO(B) atlas by Lauberts)
    # 40 - (string)	- VdBH identificator (Van den Bergh and Herbst; Catalogue of southern stars embedded in nebulosity)
    # 41 - (int)	- DWB number (Catalogue and distances of optically visible H II regions)
    # 42 - (int)	- Tr number (Trumpler Catalogue)
    # 43 - (int)	- St number (Stock Catalogue)
    # 44 - (int)	- Ru number (Ruprecht Catalogue)
    # 45 - (int)	- VdB-Ha number (van den Bergh-Hagen Catalogue)

    name_seq = ["dso_id", "ra_dd", "dec_dd", "b_mag", "v_mag", "obj_type", "morph_type", "maj_axis",
        "min_axis", "orient_angle", "redshift", "redshift_err", "parallax" ,"parallax_err",
        "distance", "distance_err",
        "NGC", "IC", "M", "C", "B", "SH2", "VDB", "RCW", "LDN", "LBN", "CR", "MEL", "PGC",
        "UCG", "CED", "ARP", "VV", "PK", "PN G", "SNR G", "ACO", "HCG", "ESO", "VDBH",
        "DWB", "TR", "SR", "RU", "VDB-HA"]

    keeper_set = {"M", "NGC", "SH2", "IC", "B", "CED", "VDB", "MEL", "CR", "RCW", "LDN", "PGC", "ACO"}

    with open(filename, "r") as fd:
        while 1:
            line = fd.readline()
            if line.startswith('# --- DATA ---'):
                break

        #print(f"First real line = {fd.readline()}")
        counter = 0
        reader = csv.DictReader(fd, fieldnames=name_seq, delimiter='\t')
        for row in reader:
            if row['dso_id'].startswith("#"):
                continue
            #for cat in keeper_set:
            #    if valid_cat_id(row[cat]):
            #        print(f"got catalog {cat} = {row[cat]}")
            insert_dso_and_catalog(conn, row, keeper_set)
    return

def load_name_and_collection_data(conn, filename):
    
    #regx = r'([a-zA-z]+)\s+([-0-9a-zA-Z]+)\s+_\(\"([-a-zA-Z ]*)"\)(?: # ([A-Z0-9, )]*))?'
    regx = r'([a-zA-z0-9]+)\s+([-.+0-9a-zA-Z]+)\s+_\(\"(.*)"\)(?: # ([A-Z0-9, )]*))?'
    rexp_compiled = re.compile(regx, re.M)

    with open(filename, "r", encoding="utf8") as fd:
        while 1:
            line = fd.readline()
            if len(line) == 0:
                break
            if line.startswith('#'):
                continue
            #parse line with regex
            #group 1 = catalog (eg NGC)
            #group 2 = cat number (eg 1234)
            #group 3 = name string, etc "Sunflower Galaxy"
            #group 4 (optional) = comma separated list of collections, eg "SIMBAD, DSW, WSG B500"
            match = rexp_compiled.match(line)
            if match is None:
                print(f"NO MATCH on {line}")
            else:
                catalog = match.group(1)
                cat_num = match.group(2)
                name = match.group(3)
                collections_list = match.group(4)
                insert_names_and_collections(conn, catalog, cat_num, name, collections_list)
    return

@dataclass
class Catalog_details:
    cat_name : str
    cat_num  : str
    cat_id   : int
    dso_id   : int
    name     : str
    name_id  : int

def fix_missing_names(conn):

    cur1 = conn.cursor() #fetching cursor
    cur2 = conn.cursor() #updating cursor

    name_data_rows = cur1.execute("""
        select catalog_dso.catalog_name, catalog_dso.catalog_num, catalog_dso.catalog_id, 
            catalog_dso.dso_id, names.name, names.name_id
        from catalog_dso
        left join names on catalog_dso.catalog_id = names.catalog_id
        order by catalog_dso.dso_id asc
    """)
    cur_dso_id = None
    group_name_count = 0
    group = []

    for row in name_data_rows:
        #we get names ordered by dso_id
        #if there are more than one name per dso_id, we may need to fix catalog entries that have missing name
        cd = Catalog_details(*row)

        if cur_dso_id is None:
            cur_dso_id = cd.dso_id
        
        if cd.dso_id != cur_dso_id:
            #new ID
            #this needs to start a new group, so process current group first
            if group_name_count > 1:
                #process this group (will add any aliases if needed)
                process_name_group(cur2, group)
            #reset group
            group = []
            group_name_count = 0

        #add this record to the current group (which might be new)
        group_name_count = group_name_count + 1
        group.append( cd )          
        cur_dso_id = cd.dso_id

    #could we end on an unprocessed group?
    if group_name_count > 1:
        #process this group (will add any aliases if needed)
        process_name_group(cur2, group)

    return

from typing import List

def process_name_group(cursor, group : List[Catalog_details]):
    unnamed = 0
    lowest_name_id = 1000000
    best_group_for_name = None
    for cd in group:
        if cd.name is None:
            unnamed += 1
        elif cd.name_id < lowest_name_id:
            lowest_name_id = cd.name_id
            best_group_for_name = cd

    if unnamed > 0 and unnamed != len(group):
        #print(f"found group that qualifies: {group} Lowest ID: {lowest_name_id}")
        for cd in group:
            if cd.name is None:
                #print(f"Ready to add name: {best_group_for_name.name} to group: {cd}")
                try:
                    cursor.execute("""
                        INSERT INTO names (catalog_name, catalog_num, name, catalog_id, dso_id)
                            VALUES (?,?,?,?,?)
                        """,
                            (cd.cat_name, cd.cat_num, best_group_for_name.name, cd.cat_id, cd.dso_id))

                except sqlite3.Error as e:
                    print(f"Exception in process name group! {e.args[0]}")

    #make sure that all Messier entries have themselves as a name
    #e.g "M 3" gets the name "M3" - many Messier's do not have any other name!
    
    for cd2 in group:
        if cd2.cat_name == 'M':
            fake_name = "M" + cd2.cat_num
            print(f"prepared to add Messier fake name {fake_name} for {cd2}")
            try:
                cursor.execute("""
                    INSERT INTO names (catalog_name, catalog_num, name, catalog_id, dso_id)
                        VALUES (?,?,?,?,?)
                    """,
                        (cd2.cat_name, cd2.cat_num, fake_name, cd2.cat_id, cd2.dso_id))

            except sqlite3.Error as e:
                print(f"Exception in process name group! {e.args[0]}")

    return

def get_all_names_for_dso_id(conn, dso_id):
    #this is a hack to deal with stupidly complex SQL
    #relies on the denormalization of dso_id into names table
    #returns a list of names, rank ordered by name_id ascending (first ID is probably best one)
    results = []
    names = conn.cursor().execute("""
            select
                n.dso_id, n.name_id, n.name
            from
                names n
            where 
                n.dso_id = ?
            order by n.dso_id, n.name_id
        """, (dso_id,))
    for row in names:
        if row[0]:
            results.append(row[2])
    return results

def get_get_catalogs_for_dso_id(conn, dso_id):
    #this is a hack to deal with stupidly complex SQL
    #relies on the denormalization of dso_id into catalog_dso table
    #returns a list of tuple (catalog_name, catalog_id) rank ordered so that best cat is on top
    #use SQL to do the ranking

    results = []
    names = conn.cursor().execute("""
        select
            cd.dso_id, cd.catalog_name, cd.catalog_num, 
            (case catalog_name 
                when "M" then 1
                when "NGC" then 2
                when "Sh2" then 3
                when "IC" then 4
                when "B" then 5
                when "CED" then 6
                when "VDB" then 7
                when "CR" then 8
                when "OC" then 9
                else 99
                end) 
            as priority
        from
            catalog_dso cd
        where
            cd.dso_id = ?
        order by cd.dso_id, priority 
        """, (dso_id,))
    for row in names:
        if row[0]:
            results.append( (row[1], row[2]) )
    return results


def dump_test(conn):
    for row in conn.cursor().execute(
        """
            select * from catalog_dso cd where cd.dso_id in (select d.dso_id from dso_detail d where d.dso_id < 10)
        """):
        print(row)


def dump_good_stuff(conn, output_filename, double_stars_filename):
    #new version that removes complexity from SQL and does post-dso-fetch query for names and catalog ids
    #I'm sure that SQL could do all this, but I couldn't get it right

    #parameters
    min_dec_dd = -35.0 #lowest elevation to be reasonably visible
    min_axis_as = 1.0   #minimum size of either axis in arcsecs
    min_v_mag = 12.0    #dimmest object (also show the 99's) - note that higher mag number == dimmer object

    #not using collection filtering, but just say ALL NAMED dsos get included
    #collection_list = ['B500', 'WK', "WP", "APOD", "NED", "SEDS", "OGSC", "DSW", "HT", "SOG", 
    #                        "PSA", "PN", "WSO", "BH", "DN", "CC","???", "ST1", "ST2", "ST3", "ST4" "ST5", "ST6"]

    data = conn.cursor().execute(
        """
        select 
            dd.dso_id, dd.ra_dd, dd.dec_dd, dd.obj_type, dd.v_mag, dd.maj_axis, dd.min_axis
        from
            dso_detail dd
        where
            dd.dso_id in (
                select distinct cd.dso_id from catalog_dso cd
                where cd.catalog_name = 'M'
            union
                select distinct n.dso_id from names n
                where n.name is not null
            union
                select distinct c.dso_id from collections c
                where c.collection_name is not null
            )
            and dd.dec_dd > ?
            and (dd.v_mag = 99 or dd.v_mag < ?) 
            and (dd.min_axis > ? or dd.maj_axis > ?)
        """, (min_dec_dd, min_v_mag, min_axis_as, min_axis_as)
    )
     
    with open (output_filename, 'w', newline='', encoding='utf8' ) as csvwriter:
        dso_writer = csv.writer(csvwriter, dialect='excel-tab')  #do we need to specify other constraints?

        #write a header row
        dso_writer.writerow(['dso_id', 'catalog', 'name', 'ra_dd', 'dec_dd', 'type', 'vis_mag', 'maj_axis', 'min_axis', 'search_name'])

        for i, row in enumerate(data):

            #fetch the best names and best catalog nums for these dsos (cheating!)
            names = get_all_names_for_dso_id(conn, int(row[0]))
            cats = get_get_catalogs_for_dso_id(conn, int(row[0]))

            best_name = names[0]
            best_cat = cats[0][0] + " " + cats[0][1]

            #13Apr2022 - if this is a Messier object, add the fake name Mnnn to the end of the best name if best name is not null
            # in other words, make it like "Andromeda M31" but leave M103 as just "M103"
            if cats[0][0] == "M":
                alt_name = "M" + cats[0][1]
                if best_name != alt_name:
                    best_name = best_name + " " + alt_name

            #13Apr - create a string optimized for searching using Validate tool
            hms = degree2HMS(row[1])
            space = hms.index(" ")
            hour = hms[0:space]
            search_name = f"{best_name} {best_cat} {hour}"

            new_dso = []
            new_dso.append( row[0]) #dso_id
            new_dso.append( best_cat )        #row[1] + " " + row[2])  #cat_name space cat_num (text)
            new_dso.append( best_name )       #row[3])  #best name
            new_dso.append( hms )  #RA in decimal degrees convert to HMS
            new_dso.append( degree2DMS(row[2]))  #DEC in decimal degrees convert to DMS
            new_dso.append( row[3])  #obj type
            new_dso.append( row[4])  #vis mag
            new_dso.append( row[5])  #maj axis in ARCMIN (not degrees)
            new_dso.append( row[6])  #min axis
            new_dso.append( search_name ) #search_name used for Validate lookup tool

            #write the row
            dso_writer.writerow(new_dso)

        #now merge in double stars
        if double_stars_filename:
            merge_in_double_stars(double_stars_filename, dso_writer)
        
    return

def merge_in_double_stars(double_star_csv_file, csvwriter):
    #assumes we have pre-created a CSV file of key double stars
    #col 0 = name, col1 = RA, col2 = DEC
    #assumes csvwriter is ready to write
    #we have to remap to that output


    with open (double_star_csv_file, 'r', newline='', encoding='utf8' ) as ds_file:
        ds_reader = csv.reader(ds_file)

        for row in ds_reader:
            hms = row[1]
            space = hms.index(" ")
            hour = str.lower(hms[0:space])
            out_row = []
            out_row.append("")          #dso_id
            out_row.append("")          #catalog + num
            out_row.append(row[0])      #ds name
            out_row.append(row[1])      #RA HMS
            out_row.append(row[2])      #DEC DMS
            out_row.append("DS")        #obj type =  Double Star
            out_row.append(99)          #vis mag just set to 99 means unknown
            out_row.append("")          #maj axis = null means unknown or NA
            out_row.append("")          #min axis
            out_row.append(f"{row[0]} {hour}") #added search_name
            
            csvwriter.writerow(out_row)
    return

def merge_in_double_stars_json(double_star_csv_file, raw_data):
    #assumes we have pre-created a CSV file of key double stars
    #col 0 = name, col1 = RA, col2 = DEC
    # add stars to raw_data, which will get converted to JSON later


    with open (double_star_csv_file, 'r', newline='', encoding='utf8' ) as ds_file:
        ds_reader = csv.reader(ds_file)
        ds_count: int = 1 # create pseudo dso_id
        
        for row in ds_reader:
            hms = row[1]
            space = hms.index(" ")
            hour = str.lower(hms[0:space])
            

            new_ds = {}
            new_ds["dso_id"] =  f"ds_{ds_count}" #dso_id
            new_ds["catalog"] = ""   # no catalog for double stars
            new_ds["name"] = row[0]        # common name
            new_ds["ra_dd"] = HMS2Degrees(row[1])  #RA from HMS to decimal degrees
            new_ds["dec_dd"] = DMS2Degrees(row[2])  #DEC from DMS to decimal degrees
            new_ds["type"] = "DS"
            new_ds["vis_mag"] = float(row[3])  #vis mag just set to 99.0 means unknown
            new_ds["maj_axis"] = ""
            new_ds["min_axis"] = ""
            new_ds["search_name"] = row[0]  #search_name

            raw_data.append(new_ds)
            ds_count += 1

    return

def classify_dso_type(obj_type: str) -> str:
    # mapping Stellarium obj_type to broader classes
    if obj_type in ["G", "Gx", "IG", "QSO", "AGx", "GiG"]:
        return "Gal"
    elif obj_type in ["GC", "OC", "CL", "Cl"]:
        return "Cls"
    elif obj_type in ["NB", "PN", "DN", "RN", "C+N", "HA", "HII", "SNR", "BN", "EN", "SA", "SC", "ISM", "GNe"]:
        return "Neb"
    elif obj_type in ["DN", "RN"]:
        return "Nova"
    elif obj_type in ["DS"]:
        return "DS"
    else:
        return "Oth"

def create_dso_table_insert_data(sqlite_dso_db_filename, raw_data:list[dict]):
    # create the SQLite database and table
    # always creates a new table
    conn = sqlite3.connect(sqlite_dso_db_filename)
    cursor = conn.cursor()
    cursor.execute('''
        DROP TABLE IF EXISTS dso;
    ''')
    cursor.execute('''
        CREATE TABLE dso (
            dso_id TEXT PRIMARY KEY,
            catalog TEXT,
            name TEXT,
            ra_dd REAL,
            dec_dd REAL,
            type TEXT,
            class TEXT,
            vis_mag REAL,
            maj_axis REAL,
            min_axis REAL,
            size TEXT,   
            constellation TEXT,
            constellation_abbr TEXT,
            search_name TEXT
        );  
    ''')

    # and insert the raw data
    for dso in raw_data:
        cursor.execute('''
            INSERT INTO dso (dso_id, catalog, name, ra_dd, dec_dd, type, class, vis_mag, maj_axis, min_axis, size,
                       constellation, constellation_abbr, search_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (dso["dso_id"], dso["catalog"], dso["name"], dso["ra_dd"], dso["dec_dd"], dso["type"], dso['class'],
                dso["vis_mag"], dso["maj_axis"], dso["min_axis"], dso["size"],
                  dso['constellation'], dso['constellation_abbr'], dso["search_name"]))
    conn.commit()
    conn.close()

def get_constellation(ra: float, dec: float) -> tuple[str, str]:
    # use astronomy engine to get constellation name and three letter symbol
    from astronomy import Constellation, ConstellationInfo
    # AE expects RA in decimal hours, dec in decimal degrees
    ra = ra / 15.0
    const: ConstellationInfo = Constellation(ra, dec)
    return (const.name, const.symbol)

def get_size_string(maj_axis: str, min_axis: str) -> str:
    # create string max x min, or just maj of that's only param
    # axis values can be nn.n or ""
    # round to integer is good enough?
    # goal is to be compact!
    if maj_axis is None and min_axis is None:
        return ""
    if maj_axis is not None and maj_axis != "":
        maj_axis_s = round(float(maj_axis))
    else:
        maj_axis_s = ""
    if min_axis is not None and min_axis != "":
        min_axis_s = round(float(min_axis))

    if maj_axis_s and min_axis_s:
        return f"{maj_axis_s}x{min_axis_s}"
    
    return str(maj_axis_s)

def dump_good_stuff_as_json(conn, output_filename, double_stars_filename, sqlite_dso_db_filename: str = ""):
    #new version that removes complexity from SQL and does post-dso-fetch query for names and catalog ids
    #I'm sure that SQL could do all this, but I couldn't get it right
    # dump as JSON with names for DSO_APP

    # sep2025 - if sqlite_db_filename is not null, also create new db and add the denormalized data there

    #parameters
    min_dec_dd = -35.0 #lowest elevation to be reasonably visible
    min_axis_as = 1.0   #minimum size of either axis in arcsecs
    min_v_mag = 12.0    #dimmest object (also show the 99's) - note that higher mag number == dimmer object

    #not using collection filtering, but just say ALL NAMED dsos get included
    #collection_list = ['B500', 'WK', "WP", "APOD", "NED", "SEDS", "OGSC", "DSW", "HT", "SOG", 
    #                        "PSA", "PN", "WSO", "BH", "DN", "CC","???", "ST1", "ST2", "ST3", "ST4" "ST5", "ST6"]

    data = conn.cursor().execute(
        """
        select 
            dd.dso_id, dd.ra_dd, dd.dec_dd, dd.obj_type, dd.v_mag, dd.maj_axis, dd.min_axis
        from
            dso_detail dd
        where
            dd.dso_id in (
                select distinct cd.dso_id from catalog_dso cd
                where cd.catalog_name = 'M'
            union
                select distinct n.dso_id from names n
                where n.name is not null
            union
                select distinct c.dso_id from collections c
                where c.collection_name is not null
            )
            and dd.dec_dd > ?
            and (dd.v_mag = 99 or dd.v_mag < ?) 
            and (dd.min_axis > ? or dd.maj_axis > ?)
        """, (min_dec_dd, min_v_mag, min_axis_as, min_axis_as)
    )
     
        #write a header row
        #dso_writer.writerow(['dso_id', 'catalog', 'name', 'ra_dd', 'dec_dd', 'type', 'vis_mag', 'maj_axis', 'min_axis', 'search_name'])

    raw_data = []

    for i, row in enumerate(data):

        #fetch the best names and best catalog nums for these dsos (cheating!)
        names = get_all_names_for_dso_id(conn, int(row[0])) #dso_id
        cats = get_get_catalogs_for_dso_id(conn, int(row[0]))

        best_name = names[0]
        best_cat = cats[0][0] + " " + cats[0][1]

        #13Apr2022 - if this is a Messier object, add the fake name Mnnn to the end of the best name if best name is not null
        # in other words, make it like "Andromeda M31" but leave M103 as just "M103"
        if cats[0][0] == "M":
            alt_name = "M" + cats[0][1]
            if best_name != alt_name:
                best_name = best_name + " " + alt_name

        #13Apr - create a string optimized for searching using Validate tool
        hms = degree2HMS(row[1]) #ra_dd is decimal RA
        space = hms.index(" ")
        hour = hms[0:space]
        search_name = f"{best_name} {best_cat} {hour}"

        new_dso = {}
        new_dso["dso_id"] =  row[0] #dso_id
        new_dso["catalog"] = best_cat        #row[1] + " " + row[2])  #cat_name space cat_num (text)
        new_dso["name"] = best_name        #row[3])  #best name
        new_dso["ra_dd"] = row[1]  #RA in decimal degrees
        new_dso["dec_dd"] = row[2]  #DEC in decimal degrees convert to DMS
        new_dso["type"] = row[3]  #obj type
        new_dso["vis_mag"] =row[4]  #vis mag
        new_dso["maj_axis"] = row[5]  #maj axis in ARCMIN (not degrees)
        new_dso["min_axis"] = row[6] #min axis
        new_dso["search_name"] = search_name  #search_name used for Validate lookup tool

        raw_data.append(new_dso)

    #now merge in double stars
    if double_stars_filename:
       merge_in_double_stars_json(double_stars_filename, raw_data)

    # add in constellation name and abbrev, and classify type
    for dso in raw_data:
        dso['constellation'], dso['constellation_abbr'] = get_constellation(dso['ra_dd'], dso['dec_dd'])
        dso['class'] = classify_dso_type(dso['type'])
        dso['size'] = get_size_string(dso['maj_axis'], dso['min_axis'])

    # create json file
    with open(output_filename, 'w') as fp:
        json.dump(raw_data, fp)

    # if sqlite_dso_db_filename is not empty, also create new db and add the denormalized data there
    if sqlite_dso_db_filename:
        create_dso_table_insert_data(sqlite_dso_db_filename, raw_data)

    return


if __name__ == "__main__":

    #13Apr2022 - added "search" column to csv output, to facilitate Sheet's Validate lookup tool
    #10Mar2023 - added json output for DSO_APP

    # Sep2025 - added creation of sqlite table as alternative to JSON file

    import os    
    print("current directory = ", os.getcwd()) #/home/david/node_projects/dso_planner

    DB_NAME = './data_extraction/stellarium_data/staging.db'
    conn = sqlite3.connect(DB_NAME)

    catalog_filename = "./data_extraction/stellarium_data/catalog.txt"
    names_filename = "./data_extraction/stellarium_data//names.dat"
    csv_output_filename = "./data_extraction/python/test_out_w_search.csv"
    json_output_filename = "./data_extraction/python/raw_json_out.json"
    csv_double_stars_filename = "./data_extraction/stellarium_data/double_stars.csv"

    # create sqlite for denormalized data to drive new web page
    sqlite_dso_db_filename = "./dso_data.db"

    with conn:
        try:
            create_tables(conn)

            load_stellarium_catalog(conn, catalog_filename)
            load_name_and_collection_data(conn, names_filename)
            fix_missing_names(conn)
        
        except sqlite3.Error as e:
            print(f"Exception! {e.args[0]}")
        
        # dump_test(conn)
        #conn.set_trace_callback(print)
        dump_good_stuff_as_json(conn, json_output_filename, csv_double_stars_filename, sqlite_dso_db_filename)

    print("done")
