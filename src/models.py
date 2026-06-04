"""Data models for Profile, Group, and Page scraped data."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class WorkExperience:
    company: str = ""
    position: str = ""
    city: str = ""
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    is_current: bool = False
    def to_str(self) -> str:
        p = []
        if self.position and self.company: p.append(f"{self.position} at {self.company}")
        elif self.company: p.append(self.company)
        if self.city: p.append(f"({self.city})")
        d = []
        if self.start_date: d.append(self.start_date)
        if self.end_date: d.append(self.end_date)
        elif self.is_current: d.append("Present")
        if d: p.append(f"[{' - '.join(d)}]")
        return " ".join(p)

@dataclass
class Education:
    school: str = ""
    type: str = ""
    concentration: str = ""
    degree: str = ""
    year: str = ""
    description: str = ""
    def to_str(self) -> str:
        p = [self.school] if self.school else []
        if self.degree and self.concentration: p.append(f"({self.degree} in {self.concentration})")
        elif self.degree: p.append(f"({self.degree})")
        elif self.concentration: p.append(f"({self.concentration})")
        if self.year: p.append(f"Class of {self.year}")
        return " ".join(p)

@dataclass
class FamilyMember:
    name: str = ""
    relationship: str = ""
    profile_url: str = ""
    def to_str(self) -> str:
        return f"{self.name} ({self.relationship})" if self.relationship else self.name

@dataclass
class GroupMember:
    name: str = ""
    user_id: str = ""
    profile_url: str = ""
    role: str = ""
    join_date: str = ""
    def to_str(self) -> str:
        return f"{self.name} [{self.role}]" if self.role else self.name

@dataclass
class BusinessHours:
    monday: str = ""; tuesday: str = ""; wednesday: str = ""
    thursday: str = ""; friday: str = ""; saturday: str = ""; sunday: str = ""
    def to_str(self) -> str:
        days = [("Mon", self.monday), ("Tue", self.tuesday), ("Wed", self.wednesday),
                ("Thu", self.thursday), ("Fri", self.friday), ("Sat", self.saturday), ("Sun", self.sunday)]
        return "; ".join(f"{d}: {h}" for d, h in days if h)


# =============================================================================
# Profile Data
# =============================================================================
@dataclass
class ProfileData:
    entity_type: str = "profile"
    profile_type: str = ""  # "Public Profile", "Locked Profile", "Private Profile", "Deactivated", "Limited Profile"
    name: str = ""
    username: str = ""
    user_id: str = ""
    profile_url: str = ""
    profile_picture_url: str = ""
    cover_photo_url: str = ""
    verified: bool = False
    bio: str = ""
    intro: str = ""
    favorite_quotes: str = ""
    work: list[WorkExperience] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    current_city: str = ""
    hometown: str = ""
    places_lived: list[str] = field(default_factory=list)
    relationship_status: str = ""
    significant_other: str = ""
    family_members: list[FamilyMember] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    websites: list[str] = field(default_factory=list)
    social_links: list[str] = field(default_factory=list)
    birthday: str = ""
    gender: str = ""
    languages: list[str] = field(default_factory=list)
    interested_in: str = ""
    political_views: str = ""
    religious_views: str = ""
    life_events: list[str] = field(default_factory=list)
    friends_count: int = 0
    followers_count: int = 0
    following_count: int = 0
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dataset_dict(self) -> dict:
        return {
            "entity_type": self.entity_type, "profile_type": self.profile_type,
            "name": self.name, "username": self.username,
            "user_id": self.user_id, "profile_url": self.profile_url,
            "profile_picture_url": self.profile_picture_url, "cover_photo_url": self.cover_photo_url,
            "verified": self.verified, "bio": self.bio, "intro": self.intro,
            "favorite_quotes": self.favorite_quotes,
            "work": [w.to_str() for w in self.work],
            "work_details": [{"company": w.company, "position": w.position, "city": w.city,
                              "start_date": w.start_date, "end_date": w.end_date, "is_current": w.is_current}
                             for w in self.work],
            "education": [e.to_str() for e in self.education],
            "education_details": [{"school": e.school, "type": e.type, "concentration": e.concentration,
                                   "degree": e.degree, "year": e.year} for e in self.education],
            "current_city": self.current_city, "hometown": self.hometown,
            "places_lived": self.places_lived,
            "relationship_status": self.relationship_status, "significant_other": self.significant_other,
            "family_members": [f.to_str() for f in self.family_members],
            "phone_numbers": self.phone_numbers, "emails": self.emails,
            "websites": self.websites, "social_links": self.social_links,
            "birthday": self.birthday, "gender": self.gender,
            "languages": self.languages, "interested_in": self.interested_in,
            "political_views": self.political_views, "religious_views": self.religious_views,
            "life_events": self.life_events,
            "friends_count": self.friends_count, "followers_count": self.followers_count,
            "following_count": self.following_count, "scraped_at": self.scraped_at,
        }

    def to_excel_row(self) -> list:
        return [self.entity_type, self.profile_type, self.name, self.username, self.user_id, self.profile_url,
                self.verified, self.bio, self.intro,
                "; ".join(w.to_str() for w in self.work),
                "; ".join(e.to_str() for e in self.education),
                self.current_city, self.hometown,
                "; ".join(self.places_lived),
                self.relationship_status, self.significant_other,
                "; ".join(f.to_str() for f in self.family_members),
                "; ".join(self.phone_numbers), "; ".join(self.emails),
                "; ".join(self.websites), "; ".join(self.social_links),
                self.birthday, self.gender, "; ".join(self.languages),
                self.religious_views, self.political_views, self.favorite_quotes,
                self.friends_count, self.followers_count, self.following_count,
                self.profile_picture_url, self.cover_photo_url, self.scraped_at]


# =============================================================================
# Group Data
# =============================================================================
@dataclass
class GroupData:
    entity_type: str = "group"
    group_type: str = ""  # "Public Group", "Private Group", "Hidden Group", "Archived Group"
    name: str = ""
    group_id: str = ""
    group_url: str = ""
    description: str = ""
    cover_photo_url: str = ""
    privacy: str = ""
    visibility: str = ""
    join_mode: str = ""
    member_count: int = 0
    posts_per_day: float = 0.0
    posts_per_month: float = 0.0
    created_at: str = ""
    admins: list[GroupMember] = field(default_factory=list)
    moderators: list[GroupMember] = field(default_factory=list)
    members: list[GroupMember] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    location: str = ""
    history: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dataset_dict(self) -> dict:
        return {
            "entity_type": self.entity_type, "group_type": self.group_type,
            "name": self.name, "group_id": self.group_id,
            "group_url": self.group_url, "description": self.description,
            "cover_photo_url": self.cover_photo_url, "privacy": self.privacy,
            "visibility": self.visibility, "join_mode": self.join_mode,
            "member_count": self.member_count, "posts_per_day": self.posts_per_day,
            "posts_per_month": self.posts_per_month, "created_at": self.created_at,
            "admins": [{"name": a.name, "role": a.role} for a in self.admins],
            "moderators": [{"name": m.name, "role": m.role} for m in self.moderators],
            "rules": self.rules, "topics": self.topics,
            "location": self.location, "history": self.history, "scraped_at": self.scraped_at,
        }

    def to_excel_row(self) -> list:
        return [self.entity_type, self.group_type, self.name, self.group_id, self.group_url, self.description,
                self.privacy, self.visibility, self.join_mode,
                self.member_count, self.posts_per_day, self.posts_per_month, self.created_at,
                "; ".join(a.to_str() for a in self.admins),
                "; ".join(m.to_str() for m in self.moderators),
                "\n".join(self.rules), "; ".join(self.topics),
                self.location, self.history, self.cover_photo_url, self.scraped_at]


# =============================================================================
# Page Data
# =============================================================================
@dataclass
class PageData:
    entity_type: str = "page"
    page_type: str = ""  # "Public Page", "Verified Page", "Business Page", "Community Page", "Unpublished Page"
    name: str = ""
    page_id: str = ""
    page_url: str = ""
    username: str = ""
    category: str = ""
    sub_categories: list[str] = field(default_factory=list)
    profile_picture_url: str = ""
    cover_photo_url: str = ""
    verified: bool = False
    description: str = ""
    short_description: str = ""
    mission: str = ""
    company_overview: str = ""
    founded: str = ""
    products: str = ""
    impressum: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    additional_websites: list[str] = field(default_factory=list)
    instagram_url: str = ""
    twitter_url: str = ""
    whatsapp_number: str = ""
    address_street: str = ""
    address_city: str = ""
    address_state: str = ""
    address_zip: str = ""
    address_country: str = ""
    full_address: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    service_area: str = ""
    hours: Optional[BusinessHours] = None
    price_range: str = ""
    likes_count: int = 0
    followers_count: int = 0
    checkins_count: int = 0
    rating: float = 0.0
    review_count: int = 0
    talking_about_count: int = 0
    page_created: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dataset_dict(self) -> dict:
        return {
            "entity_type": self.entity_type, "page_type": self.page_type,
            "name": self.name, "page_id": self.page_id,
            "page_url": self.page_url, "username": self.username, "category": self.category,
            "sub_categories": self.sub_categories,
            "profile_picture_url": self.profile_picture_url, "cover_photo_url": self.cover_photo_url,
            "verified": self.verified, "description": self.description,
            "short_description": self.short_description, "mission": self.mission,
            "company_overview": self.company_overview, "founded": self.founded,
            "products": self.products, "impressum": self.impressum,
            "phone": self.phone, "email": self.email, "website": self.website,
            "additional_websites": self.additional_websites,
            "instagram_url": self.instagram_url, "twitter_url": self.twitter_url,
            "whatsapp_number": self.whatsapp_number,
            "address_street": self.address_street, "address_city": self.address_city,
            "address_state": self.address_state, "address_zip": self.address_zip,
            "address_country": self.address_country, "full_address": self.full_address,
            "latitude": self.latitude, "longitude": self.longitude,
            "service_area": self.service_area,
            "hours": self.hours.to_str() if self.hours else "",
            "price_range": self.price_range,
            "likes_count": self.likes_count, "followers_count": self.followers_count,
            "checkins_count": self.checkins_count, "rating": self.rating,
            "review_count": self.review_count, "talking_about_count": self.talking_about_count,
            "page_created": self.page_created, "scraped_at": self.scraped_at,
        }

    def to_excel_row(self) -> list:
        return [self.entity_type, self.page_type, self.name, self.page_id, self.page_url, self.username,
                self.category, "; ".join(self.sub_categories), self.verified,
                self.description, self.short_description, self.mission, self.company_overview,
                self.founded, self.products, self.phone, self.email, self.website,
                "; ".join(self.additional_websites), self.instagram_url, self.twitter_url,
                self.whatsapp_number, self.full_address, self.address_city, self.address_state,
                self.address_country, self.latitude, self.longitude, self.service_area,
                self.hours.to_str() if self.hours else "", self.price_range,
                self.likes_count, self.followers_count, self.checkins_count,
                self.rating, self.review_count, self.talking_about_count,
                self.impressum, self.page_created, self.profile_picture_url, self.cover_photo_url,
                self.scraped_at]


# =============================================================================
# Excel Headers
# =============================================================================
PROFILE_EXCEL_HEADERS = [
    "Type", "Profile Type", "Name", "Username", "User ID", "Profile URL", "Verified", "Bio", "Intro",
    "Work", "Education", "Current City", "Hometown", "Places Lived",
    "Relationship Status", "Significant Other", "Family Members",
    "Phone Numbers", "Emails", "Websites", "Social Links",
    "Birthday", "Gender", "Languages", "Religious Views", "Political Views", "Favorite Quotes",
    "Friends Count", "Followers Count", "Following Count",
    "Profile Picture URL", "Cover Photo URL", "Scraped At",
]

GROUP_EXCEL_HEADERS = [
    "Type", "Group Type", "Name", "Group ID", "Group URL", "Description",
    "Privacy", "Visibility", "Join Mode",
    "Member Count", "Posts/Day", "Posts/Month", "Created At",
    "Admins", "Moderators",
    "Rules", "Topics", "Location", "History", "Cover Photo URL", "Scraped At",
]

PAGE_EXCEL_HEADERS = [
    "Type", "Page Type", "Name", "Page ID", "Page URL", "Username",
    "Category", "Sub-Categories", "Verified",
    "Description", "Short Description", "Mission", "Company Overview",
    "Founded", "Products", "Phone", "Email", "Website", "Additional Websites",
    "Instagram", "Twitter", "WhatsApp",
    "Full Address", "City", "State", "Country", "Latitude", "Longitude", "Service Area",
    "Hours", "Price Range",
    "Likes", "Followers", "Check-ins", "Rating", "Reviews", "Talking About",
    "Impressum", "Page Created", "Profile Picture URL", "Cover Photo URL", "Scraped At",
]
