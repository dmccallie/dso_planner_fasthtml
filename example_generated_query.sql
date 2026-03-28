
WITH dso_localized AS (
    SELECT dso.*, ld.altitude, ld.azimuth, ld.air_mass,
        ld.rise_time, ld.set_time, ld.transit_time
    FROM dso
    LEFT JOIN dso_localization_values ld ON dso.dso_id = ld.dso_id
    WHERE ld.session_id = ? AND ld.loc_hash = ?
)

SELECT * FROM 
    (SELECT d.*,
        SQRT( ( (d.ra_dd - ref.ref_ra) * COS(ref.ref_dec * PI() / 180.0) ) *
            ( (d.ra_dd - ref.ref_ra) * COS(ref.ref_dec * PI() / 180.0) ) +
            (d.dec_dd - ref.ref_dec) * (d.dec_dd - ref.ref_dec) )
        AS angular_distance_deg
        FROM dso_localized AS d 
    
        CROSS JOIN ( 
            SELECT ra_dd AS ref_ra, dec_dd AS ref_dec FROM dso_localized
            WHERE catalog = 'M 101'
            LIMIT 1 )
        AS ref
    ) AS q
    
WHERE q.type = 'GC' 
AND q.altitude > 20 
    
ORDER BY q.angular_distance_deg ASC
with params session_id=HHKttqvsr4ekSiMZ4QgMKvXNEs-1xotj,
loc_hash=bc7127b971b52e87e0a374eed9708cf969f1e5918fe2de41c066bcda2dbceacd;