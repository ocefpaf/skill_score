"""
Constants and definitions.
Now we need to specify all the names we know for water level, names that
will get used in the CSW search, and also to find data in the datasets that
are returned.  This is ugly and fragile.  There hopefully will be a better
way in the future...
Standard Library.
"""

from lxml import etree
from io import BytesIO
from warnings import warn
try:
    from urllib.request import urlopen
except ImportError:
    from urllib import urlopen

# Scientific stack.
import numpy as np
from IPython.display import HTML
from scipy.spatial import KDTree
from pandas import DataFrame, read_csv, date_range

# Custom IOOS/ASA modules (available at PyPI).
from owslib import fes
from owslib.ows import ExceptionReport
from iris.exceptions import CoordinateNotFoundError


CSW = {'NGDC Geoportal':
       'http://www.ngdc.noaa.gov/geoportal/csw',
       'USGS WHSC Geoportal':
       'http://geoport.whoi.edu/geoportal/csw',
       'NODC Geoportal: granule level':
       'http://www.nodc.noaa.gov/geoportal/csw',
       'NODC Geoportal: collection level':
       'http://data.nodc.noaa.gov/geoportal/csw',
       'NRCAN CUSTOM':
       'http://geodiscover.cgdi.ca/wes/serviceManagerCSW/csw',
       'USGS Woods Hole GI_CAT':
       'http://geoport.whoi.edu/gi-cat/services/cswiso',
       'USGS CIDA Geonetwork':
       'http://cida.usgs.gov/gdp/geonetwork/srv/en/csw',
       'USGS Coastal and Marine Program':
       'http://cmgds.marine.usgs.gov/geonetwork/srv/en/csw',
       'USGS Woods Hole Geoportal':
       'http://geoport.whoi.edu/geoportal/csw',
       'CKAN testing site for new Data.gov':
       'http://geo.gov.ckan.org/csw',
       'EPA':
       'https://edg.epa.gov/metadata/csw',
       'CWIC':
       'http://cwic.csiss.gmu.edu/cwicv1/discovery'}


def dateRange(start_date='1900-01-01', stop_date='2100-01-01',
              constraint='overlaps'):
    """Hopefully something like this will be implemented in fes soon."""
    if constraint == 'overlaps':
        propertyname = 'apiso:TempExtent_begin'
        start = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname,
                                                literal=stop_date)
        propertyname = 'apiso:TempExtent_end'
        stop = fes.PropertyIsGreaterThanOrEqualTo(propertyname=propertyname,
                                                  literal=start_date)
    elif constraint == 'within':
        propertyname = 'apiso:TempExtent_begin'
        start = fes.PropertyIsGreaterThanOrEqualTo(propertyname=propertyname,
                                                   literal=start_date)
        propertyname = 'apiso:TempExtent_end'
        stop = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname,
                                               literal=stop_date)
    return start, stop


def get_coops_longname(station):
    """Get longName for specific station from COOPS SOS using DescribeSensor
    request."""
    url = ('http://opendap.co-ops.nos.noaa.gov/ioos-dif-sos/SOS?service=SOS&'
           'request=DescribeSensor&version=1.0.0&'
           'outputFormat=text/xml;subtype="sensorML/1.0.1"&'
           'procedure=urn:ioos:station:NOAA.NOS.CO-OPS:%s') % station
    tree = etree.parse(urlopen(url))
    root = tree.getroot()
    path = "//sml:identifier[@name='longName']/sml:Term/sml:value/text()"
    namespaces = dict(sml="http://www.opengis.net/sensorML/1.0.1")
    longName = root.xpath(path, namespaces=namespaces)
    if len(longName) == 0:
        longName = station
    return longName[0]


def coops2df(collector, coops_id, sos_name):
    """Request CSV response from SOS and convert to Pandas DataFrames."""
    collector.features = [coops_id]
    collector.variables = [sos_name]
    long_name = get_coops_longname(coops_id)

    try:
        response = collector.raw(responseFormat="text/csv")
        data_df = read_csv(BytesIO(response.encode('utf-8')),
                           parse_dates=True,
                           index_col='date_time')
        col = 'water_surface_height_above_reference_datum (m)'
        if False:
            data_df['Observed Data'] = (data_df[col] -
                                        data_df['vertical_position (m)'])
        data_df['Observed Data'] = data_df[col]
    except ExceptionReport as e:
        warn("Station %s is not NAVD datum. %s" % (long_name, e))
        data_df = DataFrame()  # Assign an empty DataFrame for now.

    data_df.name = long_name
    return data_df


def mod_df(arr, timevar, istart, istop,
           jd_start, jd_stop, mod_name):
    """Return time series (DataFrame) from model interpolated onto uniform time
    base."""
    t = timevar.points[istart:istop]
    jd = timevar.units.num2date(t)

    # Eliminate any data that is closer together than 10 seconds this was
    # required to handle issues with CO-OPS aggregations, I think because they
    # use floating point time in hours, which is not very accurate, so the
    # FMRC aggregation is aggregating points that actually occur at the same
    # time.  # FIXME: Can the interpolation take care of that?
    dt = np.diff(jd)
    s = np.array([ele.seconds for ele in dt])
    ind = np.where(s > 10)[0]  # FIXME: Use boolean.
    arr = arr[ind+1]  # FIXME: Why the +1?
    jd = jd[ind+1]  # FIXME: Why the +1?

    b = DataFrame(arr, index=jd, columns=[mod_name])
    b.dropna(inplace=True)  # Faster and more verbose than:
    # b = b[np.isfinite(b[mod_name])]
    # Interpolate onto uniform time base, fill gaps up to:
    # (10 values @ 6 min = 1 hour).
    # FIXME: Improve this part to not depend on globals jd_start, jd_stop.
    new_index = date_range(start=jd_start, end=jd_stop, freq='6Min')
    return b.reindex(new_index).interpolate(limit=10)


def service_urls(records, service='odp:url'):
    """Extract service_urls of a specific type (DAP, SOS) from records."""
    service_string = 'urn:x-esri:specification:ServiceType:' + service
    urls = []
    for key, rec in records.iteritems():
        # Create a generator object, and iterate through it until the match is
        # found if not found, gets the default value (here "none").
        url = next((d['url'] for d in rec.references if
                    d['scheme'] == service_string), None)
        if url is not None:
            urls.append(url)
    return urls


def get_nearest(cube, xi, yi, max_dist=0.04):
    """Find model data near station data xi, yi."""
    x = cube.coord(axis='X').points
    y = cube.coord(axis='Y').points
    if cube.ndim == 3:  # Structured model
        if (x.ndim == 1) and (y.ndim == 1):
            x, y = np.meshgrid(x, y)
    tree = KDTree(zip(x.ravel(), y.ravel()))
    dist, indices = tree.query(np.array([xi, yi]).T)
    if cube.ndim == 3:  # Structured model
        i, j = np.unravel_index(indices, x.shape)
    else:
        i = j = indices
    mask = dist <= max_dist
    return dist[mask], i[mask], j[mask]


def find_timevar(cube):
    """Return the time variable from Iris. This is a workaround for iris having
    problems with FMRC aggregations, which produce two time coordinates."""
    try:
        cube.coord(axis='T').rename('time')
    except CoordinateNotFoundError:
        pass
    timevar = cube.coord('time')
    return timevar


def get_coordinates(bounding_box, bounding_box_type):
    """Create bounding box coordinates for the map."""
    coordinates = []
    if bounding_box_type is "box":
        coordinates.append([bounding_box[0][1], bounding_box[0][0]])
        coordinates.append([bounding_box[0][1], bounding_box[1][0]])
        coordinates.append([bounding_box[1][1], bounding_box[1][0]])
        coordinates.append([bounding_box[1][1], bounding_box[0][0]])
        coordinates.append([bounding_box[0][1], bounding_box[0][0]])
        return coordinates


def inline_map(m):
    """From http://nbviewer.ipython.org/gist/rsignell-usgs/
    bea6c0fe00a7d6e3249c."""
    m._build_map()
    srcdoc = m.HTML.replace('"', '&quot;')
    embed = HTML('<iframe srcdoc="{srcdoc}" '
                 'style="width: 100%; height: 500px; '
                 'border: none"></iframe>'.format(srcdoc=srcdoc))
    return embed
