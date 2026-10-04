"""Shared low-level primitives for the organizer package.

Logging setup, optional third-party imports (tqdm, features.*), constants,
and process-wide caches/globals. Lowest-level module: everything else may
import from here, nothing here imports from the package.
"""
import logging
import threading
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import tqdm for progress bars
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    tqdm = None
    TQDM_AVAILABLE = False
    logger.warning("tqdm not available. Install with: pip install tqdm")

# Optional advanced features
ADVANCED_FEATURES = {}
analyze_photo_patterns = None
create_analytics_visualizations = None
generate_posting_schedule = None
auto_enhance_image = None
HashtagOptimizer = None
create_platform_variants = None
try:
    from features.analytics import analyze_photo_patterns, generate_analytics_report, create_analytics_visualizations
    ADVANCED_FEATURES['analytics'] = True
except ImportError:
    ADVANCED_FEATURES['analytics'] = False

try:
    from features.hashtag_intelligence import HashtagOptimizer
    ADVANCED_FEATURES['hashtag_optimizer'] = True
except ImportError:
    ADVANCED_FEATURES['hashtag_optimizer'] = False

try:
    from features.multi_platform import create_platform_variants
    ADVANCED_FEATURES['multi_platform'] = True
except ImportError:
    ADVANCED_FEATURES['multi_platform'] = False

try:
    from features.scheduling import generate_posting_schedule
    ADVANCED_FEATURES['scheduling'] = True
except ImportError:
    ADVANCED_FEATURES['scheduling'] = False

try:
    from features.image_enhancement import auto_enhance_image
    ADVANCED_FEATURES['image_enhancement'] = True
except ImportError:
    ADVANCED_FEATURES['image_enhancement'] = False

# Constants
SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.heic', '.heif')
CONVERTED_FORMATS = ('.png', '.jpg', '.jpeg')
THUMBNAIL_SIZE = (1024, 1024)

# Global cache for AI analysis results
_analysis_cache = {}
_cache_lock = threading.Lock()


