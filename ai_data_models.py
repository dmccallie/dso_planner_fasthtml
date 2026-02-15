# data structured needed for AI planner and tools
# contains structures for both the mult-agent and single-agent versions

from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, PositiveFloat, Field
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.models.google import GoogleModelSettings
from google.genai.types import ThinkingConfigDict
import datetime

# model_string = "openai:gpt-5.1"  # default model string for agents using these data models
# model_string = "gemini-3-pro-preview"  # alternative model string for agents using these data models
# model_string = "gemini-2.5-flash"  # alternative model string for agents using these data models

# model_string = "groq:llama-3.3-70b-versatile"  # alternative model string for agents using these data models
# model_string = "groq:openai/gpt-oss-120b"  # default model string for agents using these data models

# model_string =  "openai-responses:gpt-5.2" #'groq:llama-3.3-70b-versatile' # "gemini-3-pro-preview" #"openai:gpt-5.1"
# model_settings = OpenAIResponsesModelSettings(
#     temperature = 0.4,
#     openai_reasoning_effort="medium",
# )

model_string = "gemini-3-flash-preview"
google_thinking_config = ThinkingConfigDict(
        include_thoughts=False,
        thinking_budget=2000, # what does this mean?
    )
model_settings = GoogleModelSettings(
    temperature=0.4,  # does this will work for Gemini models?
    google_thinking_config=google_thinking_config
)

default_timezone = "America/Chicago" # for use in default defaults
@dataclass
# dependencies get shared to all tools and agents
# at run time, update these values as needed
class AstroDependencies:
    # various defaults for observer context
    default_location: str = "Powell Observatory, Kansas" # this should be findable and is near the default lat/long
    default_latitude: float = 38.6463 # 38.7076
    default_longitude: float = -94.7000 # -94.7073
    # FIXME this should reflect the browser not the server!
    default_date: str = datetime.datetime.now(ZoneInfo(default_timezone)).strftime("%Y-%m-%d")
    default_time: str = datetime.datetime.now(ZoneInfo(default_timezone)).strftime("%H:%M") # now time in HH:MM
    default_timezone: str = default_timezone
    # defaults for equipment
    default_telescope: str = "Astrophysics 130EDF F6.3"
    default_camera: str = "ZWO ASI 2600MC Pro"
    db_path: str = "./dso_data.db"  # path to local deep space object database

class Camera(BaseModel):
    name: str
    sensor_w_mm: PositiveFloat
    sensor_h_mm: PositiveFloat
    pixel_um: PositiveFloat

class Telescope(BaseModel):
    name: str
    focal_length_mm: PositiveFloat
    f_ratio: PositiveFloat

class Equipment(BaseModel):
    telescope: Optional[Telescope] = None
    camera: Optional[Camera] = None

class ObserverContext(BaseModel):
    location: str = Field(..., description="The location and/or site name as specified by the user, in string form. For example: 'Backyard in Stilwell, KS'.")
    latitude_deg: float | None = Field(default=None, description="The latitude in  degrees. If not available leave as None.")
    longitude_deg: float | None = Field(default=None, description="The longitude in  degrees. If not available leave as None.")
    observe_date: str | None = Field(default=None, description="The observation date, in local terms, as a string e.g. '2024-06-15'. If not available leave as None.")
    observe_time: str | None = Field(default=None, description="The observe time, in local hours as a string e.g. '22:00' or `8PM`. If not available leave as None.")
    #end_time_local: str | None = Field(default=None, description="The local end time in hours of the observation session as a string, e.g. '02:00'. If not available leave as None.")
    timezone: str | None = Field(default=None, description="The timezone of the observation session as a string, e.g., 'America/Chicago'. If not available leave as None.")

# define a deep space object, which will generally be the focus of an amateur astronomy session
class DeepSpaceObject(BaseModel):
    dso_id: str = Field(..., description="The unique identifier of the deep space object")
    name: str = Field(..., description="The most common name of the deep space object")
    clasz: str = Field(..., description=
        """
        The class of deep space object.
          We use 'clasz' to avoid python reserved keyword.
          We use 'clasz' for high-level classification and 'type' for more specific types.
        Our database uses the following classes (name, and db abbreviation):
        - Galaxy (Gal)
        - Nebula (Neb)
        - Cluster (Cls)
        - DS (Double Star)
        - Other (OTH)
        """
    )
    type: str = Field(..., description=
        """
        A more specific type of the deep space object, more specific than 'clasz'.
          We use (name, and db abbreviation):
        - Open Cluster (OC)
        - Globular Cluster (GC)
        - Planetary Nebula (PN)
        - Emission Nebula (HII)
        - Reflection Nebula (RN)
        - Cluster Nebula (C+N)
        - Dark Nebula (DN)
        - Supernova Remnant (SNR)
        - Galaxy (Gx)
        - Other (OTH)
        """
    )
    constellation: str = Field(..., description="The constellation in which the object is located")
    constellation_abbr: str = Field(..., description="The standard 3-letter abbreviation of the constellation")
    vis_mag: float = Field(..., description="The apparent visual magnitude (brightness) of the object")
    maj_axis: float = Field(..., description="The size of the major axis in arcminutes")
    min_axis: float = Field(..., description="The size of the minor axis in arcminutes")
    size: str = Field(..., description="A textual size description of the object, e.g. '5x10' or just '10'. Units are arcminutes.")
    ra_dd: float = Field(..., description="The right ascension in decimal degrees")
    dec_dd: float = Field(..., description="The declination in decimal degrees")
    catalog: str = Field(..., description=
                         """
                         The primary catalog designation of the object.
                         E.g., 'M 31', 'NGC 1976', 'Caldwell14', etc.
                         The catalog prefix should be included.
                         The most common catalog names are:
                         - Messier (M)
                         - New General Catalog (NGC)
                         - Index Catalog (IC)
                         - Caldwell (Caldwell)
                         - SH2 (Sharpless)
                         """)
    azimuth: float | None = Field(default=None, description="The azimuth in degrees")
    altitude: float | None = Field(default=None, description="The altitude in degrees")
    air_mass: float | None = Field(default=None, description="The air mass at the time of observation")
    rise_time: str | None = Field(default=None, description="The local time of rise in HH:MM format")
    set_time: str | None = Field(default=None, description="The local time of set in HH:MM format")
    transit_time: str | None = Field(default=None, description="The local time of transit in HH:MM format")

# define Plan, containing one or more DSOs, observation date, location, and equipment
# start with DSO for testing

class DeepSpaceObjectID(BaseModel):
    dso_id: str = Field(..., description="The unique identifier of the deep space object")
    info: str = Field(..., description="Summary information of the deep space object created by program code")

class Plan(BaseModel):
    dsos: list[DeepSpaceObject] = Field(..., description="List of deep space objects to observe in this plan")
    equipment: Equipment = Field(..., description="The equipment to be used for the observation session")
    observer_context: ObserverContext = Field(..., description="The observer's context (lat, long, date, times) for the observation session")

# single agent plan structure
# this is based on returning observer context, equipment, and sql query string
# SA means "single agent"
# this approach replaced most of the above attempts at multi-agent structures, 
#   but we keep those for now since they may be useful for future multi-agent versions
# Just return the SQL query and the observer context and equipment info 
#  flag the plan for validity to alert user
class SA_Plan(BaseModel):
    valid_plan: bool = Field(..., description="Whether the generated plan is valid and executable")
    observer_context: ObserverContext = Field(..., description="The observer's context (lat, long, date, times) for the observation session")
    equipment: Equipment = Field(..., description="The equipment to be used for the observation session")
    sql_query: str = Field(..., description="The generated SQL query string to find suitable deep space objects based on user criteria")

class EquipmentQuery(BaseModel):
    text: str

class ObserverContextQuery(BaseModel):
    text: str

