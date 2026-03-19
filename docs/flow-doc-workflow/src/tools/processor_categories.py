"""
Processor categorization system for NiFi flow documentation.

Copy this file to: nifi_mcp_server/processor_categories.py

Categories are used to:
1. Determine which processors need deep LLM analysis
2. Group processors in documentation output
3. Identify data flow entry/exit points
"""

from typing import Dict, List, Set, Tuple
from enum import Enum
import re


class ProcessorCategory(Enum):
    """Processor categories for documentation."""
    IO_READ = "IO_READ"       # Data ingestion (GetFile, ConsumeKafka, etc.)
    IO_WRITE = "IO_WRITE"     # Data output (PutFile, PublishKafka, etc.)
    LOGIC = "LOGIC"           # Business logic (RouteOnAttribute, etc.)
    TRANSFORM = "TRANSFORM"   # Data transformation (Jolt, ConvertRecord, etc.)
    VALIDATION = "VALIDATION" # Data validation (ValidateRecord, etc.)
    ENRICHMENT = "ENRICHMENT" # Data enrichment (LookupRecord, etc.)
    CONTROL = "CONTROL"       # Flow control (Wait, Notify, ControlRate, etc.)
    MONITORING = "MONITORING" # Logging/monitoring (LogAttribute, etc.)
    OTHER = "OTHER"           # Uncategorized


# Category definitions with pattern matching
# Patterns support:
#   - Exact match: "GetFile"
#   - Prefix match: "Get*" 
#   - Suffix match: "*Record"
#   - Contains: "*SQL*"
#   - Regex: "r:Execute(Script|Groovy|Python)"

PROCESSOR_CATEGORY_PATTERNS: Dict[ProcessorCategory, List[str]] = {
    
    ProcessorCategory.IO_READ: [
        # File-based
        "GetFile", "GetSFTP", "GetFTP", "GetHDFS", "GetS3Object", "GetAzureBlobStorage",
        "GetGCSObject", "ListFile", "ListSFTP", "ListS3", "ListHDFS",
        # Message queues
        "ConsumeKafka*", "ConsumeJMS", "ConsumeAMQP", "ConsumeMQTT", "GetJMSQueue",
        # Databases
        "QueryDatabaseTable", "GenerateTableFetch", "ExecuteSQL", "ExecuteSQLRecord",
        "GetMongo", "GetCouchbaseKey", "GetElasticsearch", "GetSolr",
        # HTTP/Network
        "ListenHTTP", "ListenTCP", "ListenUDP", "ListenSyslog", "GetHTTP",
        "HandleHttpRequest",
        # Other sources
        "GenerateFlowFile", "GetTwitter", "ListDatabaseTables",
    ],
    
    ProcessorCategory.IO_WRITE: [
        # File-based
        "PutFile", "PutSFTP", "PutFTP", "PutHDFS", "PutS3Object", "PutAzureBlobStorage",
        "PutGCSObject",
        # Message queues
        "PublishKafka*", "PublishJMS", "PublishAMQP", "PublishMQTT", "PutJMS",
        # Databases
        "PutDatabaseRecord", "PutSQL", "PutMongo", "PutCouchbaseKey",
        "PutElasticsearchRecord", "PutSolr",
        # HTTP/Network
        "InvokeHTTP", "PostHTTP", "PutTCP", "PutUDP", "HandleHttpResponse",
        # Email
        "PutEmail", "SendEmail",
    ],
    
    ProcessorCategory.LOGIC: [
        # Routing
        "RouteOnAttribute", "RouteOnContent", "RouteText", "RouteonAttribute",
        # Querying/Filtering
        "QueryRecord", "QueryFlowFile", "FilterRecord",
        # Attribute manipulation
        "UpdateAttribute", "UpdateRecord", "EvaluateJsonPath", "EvaluateXPath",
        "EvaluateXQuery", "ExtractText", "HashAttribute", "HashContent",
        # Decision making
        "DetectDuplicate", "DistributeLoad", "PartitionRecord",
    ],
    
    ProcessorCategory.TRANSFORM: [
        # Format conversion
        "ConvertRecord", "ConvertJSONToSQL", "ConvertAvroToJSON", "ConvertCSVToAvro",
        "TransformXml",
        # Jolt transformations
        "JoltTransformJSON", "JoltTransformRecord",
        # Script-based
        "ExecuteScript", "ExecuteGroovyScript", "ExecuteStreamCommand",
        "r:Execute(Script|Groovy|Python|Ruby)Script",  # Regex pattern
        # Content manipulation
        "ReplaceText", "ReplaceTextWithMapping", "ModifyBytes",
        "SplitJson", "SplitXml", "SplitText", "SplitRecord", "SplitContent",
        "MergeContent", "MergeRecord", "CompressContent", "UnpackContent",
        "EncryptContent", "DecryptContent", "Base64EncodeContent", "Base64DecodeContent",
    ],
    
    ProcessorCategory.VALIDATION: [
        "ValidateRecord", "ValidateJson", "ValidateXml", "ValidateCsv",
        "SchemaValidation",
    ],
    
    ProcessorCategory.ENRICHMENT: [
        "LookupRecord", "LookupAttribute", "GeoEnrichIP", "GeoEnrichIPRecord",
        "QueryRecord",  # Can be both LOGIC and ENRICHMENT depending on use
    ],
    
    ProcessorCategory.CONTROL: [
        "Wait", "Notify", "ControlRate", "MonitorActivity",
        "RetryFlowFile", "RouteOnAttribute",  # When used for error handling
        "UpdateCounter", "FetchDistributedMapCache", "PutDistributedMapCache",
    ],
    
    ProcessorCategory.MONITORING: [
        "LogAttribute", "LogMessage", "PutSlack", "PutEmail",  # When used for alerts
        "ReportingTask*",
    ],
}

# Processors that need deep LLM analysis (contain business logic worth documenting)
ANALYZABLE_CATEGORIES: Set[ProcessorCategory] = {
    ProcessorCategory.LOGIC,
    ProcessorCategory.TRANSFORM,
    ProcessorCategory.VALIDATION,
    ProcessorCategory.ENRICHMENT,
}

# Processors to always include in documentation (entry/exit points)
ALWAYS_DOCUMENT_CATEGORIES: Set[ProcessorCategory] = {
    ProcessorCategory.IO_READ,
    ProcessorCategory.IO_WRITE,
}


class ProcessorCategorizer:
    """Categorizes NiFi processors with pattern matching and tracking."""
    
    def __init__(self):
        self._compiled_patterns: Dict[ProcessorCategory, List[re.Pattern]] = {}
        self._unclassified_types: Set[str] = set()
        self._classification_cache: Dict[str, ProcessorCategory] = {}
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile pattern strings to regex patterns."""
        for category, patterns in PROCESSOR_CATEGORY_PATTERNS.items():
            compiled = []
            for pattern in patterns:
                if pattern.startswith("r:"):
                    # Explicit regex
                    compiled.append(re.compile(pattern[2:], re.IGNORECASE))
                elif "*" in pattern:
                    # Wildcard pattern - convert to regex
                    regex = pattern.replace("*", ".*")
                    compiled.append(re.compile(f"^{regex}$", re.IGNORECASE))
                else:
                    # Exact match
                    compiled.append(re.compile(f"^{re.escape(pattern)}$", re.IGNORECASE))
            self._compiled_patterns[category] = compiled
    
    def categorize(self, processor_type: str) -> ProcessorCategory:
        """
        Categorize a processor type.
        
        Args:
            processor_type: Full processor type (e.g., "org.apache.nifi.processors.standard.RouteOnAttribute")
            
        Returns:
            ProcessorCategory for the processor
        """
        # Extract simple name from full type
        simple_name = processor_type.split(".")[-1] if "." in processor_type else processor_type
        
        # Check cache first
        if simple_name in self._classification_cache:
            return self._classification_cache[simple_name]
        
        # Try to match against patterns
        for category, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.match(simple_name):
                    self._classification_cache[simple_name] = category
                    return category
        
        # No match found - track as unclassified
        self._unclassified_types.add(processor_type)
        self._classification_cache[simple_name] = ProcessorCategory.OTHER
        return ProcessorCategory.OTHER
    
    def categorize_batch(self, processor_types: List[str]) -> Dict[str, ProcessorCategory]:
        """Categorize multiple processors at once."""
        return {pt: self.categorize(pt) for pt in processor_types}
    
    def get_unclassified(self) -> Set[str]:
        """Get set of processor types that couldn't be categorized."""
        return self._unclassified_types.copy()
    
    def clear_unclassified(self):
        """Clear the unclassified tracking set."""
        self._unclassified_types.clear()
    
    def needs_llm_analysis(self, processor_type: str) -> bool:
        """Check if this processor type needs LLM analysis."""
        return self.categorize(processor_type) in ANALYZABLE_CATEGORIES
    
    def should_always_document(self, processor_type: str) -> bool:
        """Check if this processor should always appear in documentation."""
        return self.categorize(processor_type) in ALWAYS_DOCUMENT_CATEGORIES
    
    def get_category_summary(self, processors: List[Dict]) -> Dict[str, List[str]]:
        """
        Get processors grouped by category.
        
        Args:
            processors: List of processor entities with 'component.type' field
            
        Returns:
            Dict mapping category name to list of processor names
        """
        result = {cat.value: [] for cat in ProcessorCategory}
        
        for proc in processors:
            proc_type = proc.get("component", {}).get("type", "")
            proc_name = proc.get("component", {}).get("name", "Unknown")
            category = self.categorize(proc_type)
            result[category.value].append(proc_name)
        
        # Remove empty categories
        return {k: v for k, v in result.items() if v}


# Global instance for convenience
_categorizer: ProcessorCategorizer = None

def get_categorizer() -> ProcessorCategorizer:
    """Get or create the global processor categorizer."""
    global _categorizer
    if _categorizer is None:
        _categorizer = ProcessorCategorizer()
    return _categorizer


def report_unclassified_processors(
    processors: List[Dict],
    log_to_file: bool = True
) -> List[Dict[str, Any]]:
    """
    Identify and report processors that don't match any category.
    
    Returns list of unclassified processor info for review.
    """
    categorizer = get_categorizer()
    unclassified = []
    
    for proc in processors:
        proc_type = proc.get("component", {}).get("type", "")
        if categorizer.categorize(proc_type) == ProcessorCategory.OTHER:
            unclassified.append({
                "type": proc_type,
                "name": proc.get("component", {}).get("name", ""),
                "id": proc.get("id", ""),
                "simple_name": proc_type.split(".")[-1] if "." in proc_type else proc_type
            })
    
    if log_to_file and unclassified:
        from loguru import logger
        logger.warning(f"Found {len(unclassified)} unclassified processor types:")
        for item in unclassified:
            logger.warning(f"  - {item['simple_name']} ({item['name']})")
    
    return unclassified

