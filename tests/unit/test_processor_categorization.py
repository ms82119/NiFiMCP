"""Unit tests for processor categorization logic."""

import pytest
from nifi_mcp_server.processor_categories import (
    ProcessorCategory,
    ProcessorCategorizer,
    get_categorizer,
    report_unclassified_processors
)


class TestProcessorCategorization:
    """Test heuristic categorization of processors."""
    
    def setup_method(self):
        """Set up a fresh categorizer for each test."""
        self.categorizer = ProcessorCategorizer()
    
    @pytest.mark.parametrize("processor_type,expected_category", [
        # IO_READ
        ("org.apache.nifi.processors.standard.GetFile", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.kafka.pubsub.ConsumeKafka", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.GetSFTP", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.ListFile", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.ListSFTP", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.QueryDatabaseTable", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.ListenHTTP", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.GenerateFlowFile", ProcessorCategory.IO_READ),
        
        # IO_WRITE
        ("org.apache.nifi.processors.standard.PutFile", ProcessorCategory.IO_WRITE),
        ("org.apache.nifi.processors.kafka.pubsub.PublishKafka", ProcessorCategory.IO_WRITE),
        ("org.apache.nifi.processors.standard.PutSFTP", ProcessorCategory.IO_WRITE),
        ("org.apache.nifi.processors.standard.InvokeHTTP", ProcessorCategory.IO_WRITE),
        ("org.apache.nifi.processors.standard.PutEmail", ProcessorCategory.IO_WRITE),
        
        # LOGIC
        ("org.apache.nifi.processors.standard.RouteOnAttribute", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.RouteOnContent", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.UpdateAttribute", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.QueryRecord", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.FilterRecord", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.DetectDuplicate", ProcessorCategory.LOGIC),
        
        # TRANSFORM
        ("org.apache.nifi.processors.standard.JoltTransformJSON", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ConvertRecord", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ConvertAvroToJSON", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ExecuteScript", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ExecuteGroovyScript", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ReplaceText", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.SplitJson", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.MergeContent", ProcessorCategory.TRANSFORM),
        
        # VALIDATION
        ("org.apache.nifi.processors.standard.ValidateRecord", ProcessorCategory.VALIDATION),
        ("org.apache.nifi.processors.standard.ValidateJson", ProcessorCategory.VALIDATION),
        ("org.apache.nifi.processors.standard.ValidateXml", ProcessorCategory.VALIDATION),
        
        # ENRICHMENT
        ("org.apache.nifi.processors.standard.LookupRecord", ProcessorCategory.ENRICHMENT),
        ("org.apache.nifi.processors.standard.LookupAttribute", ProcessorCategory.ENRICHMENT),
        
        # CONTROL
        ("org.apache.nifi.processors.standard.Wait", ProcessorCategory.CONTROL),
        ("org.apache.nifi.processors.standard.Notify", ProcessorCategory.CONTROL),
        ("org.apache.nifi.processors.standard.ControlRate", ProcessorCategory.CONTROL),
        
        # MONITORING
        ("org.apache.nifi.processors.standard.LogAttribute", ProcessorCategory.MONITORING),
        ("org.apache.nifi.processors.standard.LogMessage", ProcessorCategory.MONITORING),
        
        # OTHER (unknown)
        ("com.custom.SomeCustomProcessor", ProcessorCategory.OTHER),
        ("org.unknown.UnknownProcessor", ProcessorCategory.OTHER),
    ])
    def test_categorize_processor(self, processor_type, expected_category):
        """Test processor type categorization."""
        result = self.categorizer.categorize(processor_type)
        assert result == expected_category, \
            f"Expected {expected_category.value} for {processor_type}, got {result.value}"
    
    def test_wildcard_patterns(self):
        """Test wildcard pattern matching."""
        # ConsumeKafka* should match ConsumeKafka_2_6, ConsumeKafka_2_0, etc.
        result1 = self.categorizer.categorize("org.apache.nifi.processors.kafka.ConsumeKafka_2_6")
        assert result1 == ProcessorCategory.IO_READ
        
        result2 = self.categorizer.categorize("org.apache.nifi.processors.kafka.ConsumeKafka_2_0")
        assert result2 == ProcessorCategory.IO_READ
        
        # PublishKafka* should match variants
        result3 = self.categorizer.categorize("org.apache.nifi.processors.kafka.PublishKafka_2_6")
        assert result3 == ProcessorCategory.IO_WRITE
    
    def test_regex_patterns(self):
        """Test regex pattern matching for Execute*Script variants."""
        # ExecuteScript, ExecuteGroovyScript, ExecutePythonScript should all match
        result1 = self.categorizer.categorize("org.apache.nifi.processors.standard.ExecuteScript")
        assert result1 == ProcessorCategory.TRANSFORM
        
        result2 = self.categorizer.categorize("org.apache.nifi.processors.standard.ExecuteGroovyScript")
        assert result2 == ProcessorCategory.TRANSFORM
        
        result3 = self.categorizer.categorize("org.apache.nifi.processors.standard.ExecutePythonScript")
        assert result3 == ProcessorCategory.TRANSFORM
    
    def test_unclassified_tracking(self):
        """Test that unclassified processors are tracked."""
        self.categorizer.clear_unclassified()  # Start fresh
        self.categorizer.categorize("com.unknown.NewProcessor")
        unclassified = self.categorizer.get_unclassified()
        assert "com.unknown.NewProcessor" in unclassified
        assert len(unclassified) == 1
    
    def test_unclassified_not_in_cache(self):
        """Test that unclassified processors are cached as OTHER."""
        self.categorizer.clear_unclassified()
        result1 = self.categorizer.categorize("com.unknown.Processor1")
        result2 = self.categorizer.categorize("com.unknown.Processor1")  # Same again
        
        # Should return OTHER both times
        assert result1 == ProcessorCategory.OTHER
        assert result2 == ProcessorCategory.OTHER
        
        # Should only be tracked once
        unclassified = self.categorizer.get_unclassified()
        assert "com.unknown.Processor1" in unclassified
        assert len(unclassified) == 1
    
    def test_categorize_batch(self):
        """Test batch categorization."""
        processor_types = [
            "org.apache.nifi.processors.standard.GetFile",
            "org.apache.nifi.processors.standard.PutFile",
            "org.apache.nifi.processors.standard.RouteOnAttribute",
            "com.unknown.CustomProcessor"
        ]
        
        results = self.categorizer.categorize_batch(processor_types)
        
        assert results["org.apache.nifi.processors.standard.GetFile"] == ProcessorCategory.IO_READ
        assert results["org.apache.nifi.processors.standard.PutFile"] == ProcessorCategory.IO_WRITE
        assert results["org.apache.nifi.processors.standard.RouteOnAttribute"] == ProcessorCategory.LOGIC
        assert results["com.unknown.CustomProcessor"] == ProcessorCategory.OTHER
    
    def test_needs_llm_analysis(self):
        """Test needs_llm_analysis method."""
        # LOGIC processors need analysis
        assert self.categorizer.needs_llm_analysis("org.apache.nifi.processors.standard.RouteOnAttribute") is True
        
        # TRANSFORM processors need analysis
        assert self.categorizer.needs_llm_analysis("org.apache.nifi.processors.standard.JoltTransformJSON") is True
        
        # IO_READ processors don't need analysis
        assert self.categorizer.needs_llm_analysis("org.apache.nifi.processors.standard.GetFile") is False
        
        # IO_WRITE processors don't need analysis
        assert self.categorizer.needs_llm_analysis("org.apache.nifi.processors.standard.PutFile") is False
    
    def test_should_always_document(self):
        """Test should_always_document method."""
        # IO_READ should always be documented
        assert self.categorizer.should_always_document("org.apache.nifi.processors.standard.GetFile") is True
        
        # IO_WRITE should always be documented
        assert self.categorizer.should_always_document("org.apache.nifi.processors.standard.PutFile") is True
        
        # LOGIC doesn't need to always be documented (but may be)
        assert self.categorizer.should_always_document("org.apache.nifi.processors.standard.RouteOnAttribute") is False
    
    def test_get_category_summary(self):
        """Test get_category_summary method."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "GetFile",
                    "type": "org.apache.nifi.processors.standard.GetFile"
                }
            },
            {
                "id": "proc2",
                "component": {
                    "name": "RouteData",
                    "type": "org.apache.nifi.processors.standard.RouteOnAttribute"
                }
            },
            {
                "id": "proc3",
                "component": {
                    "name": "PutFile",
                    "type": "org.apache.nifi.processors.standard.PutFile"
                }
            }
        ]
        
        summary = self.categorizer.get_category_summary(processors)
        
        assert "IO_READ" in summary
        assert "GetFile" in summary["IO_READ"]
        assert "IO_WRITE" in summary
        assert "PutFile" in summary["IO_WRITE"]
        assert "LOGIC" in summary
        assert "RouteData" in summary["LOGIC"]
    
    def test_get_categorizer_singleton(self):
        """Test that get_categorizer returns a singleton."""
        cat1 = get_categorizer()
        cat2 = get_categorizer()
        
        # Should be the same instance
        assert cat1 is cat2
    
    def test_report_unclassified_processors(self):
        """Test report_unclassified_processors function."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "KnownProcessor",
                    "type": "org.apache.nifi.processors.standard.GetFile"
                }
            },
            {
                "id": "proc2",
                "component": {
                    "name": "UnknownProcessor",
                    "type": "com.unknown.CustomProcessor"
                }
            }
        ]
        
        unclassified = report_unclassified_processors(processors, log_to_file=False)
        
        assert len(unclassified) == 1
        assert unclassified[0]["type"] == "com.unknown.CustomProcessor"
        assert unclassified[0]["name"] == "UnknownProcessor"
        assert unclassified[0]["id"] == "proc2"
        assert unclassified[0]["simple_name"] == "CustomProcessor"
    
    def test_simple_name_extraction(self):
        """Test that simple names are extracted correctly from full types."""
        # Full type
        result1 = self.categorizer.categorize("org.apache.nifi.processors.standard.GetFile")
        assert result1 == ProcessorCategory.IO_READ
        
        # Simple name only (should still work)
        result2 = self.categorizer.categorize("GetFile")
        assert result2 == ProcessorCategory.IO_READ
        
        # Both should be cached
        result3 = self.categorizer.categorize("org.apache.nifi.processors.standard.GetFile")
        assert result3 == ProcessorCategory.IO_READ

